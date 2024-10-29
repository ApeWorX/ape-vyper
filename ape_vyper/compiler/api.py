import os
import shutil
import time
from base64 import b64encode
from collections.abc import Iterable, Iterator
from functools import cached_property
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import vvm  # type: ignore
from ape.api import CompilerAPI, PluginConfig, TraceAPI
from ape.logging import logger
from ape.managers import ProjectManager
from ape.managers.project import LocalProject
from ape.utils import get_full_extension, get_relative_path
from ape.utils._github import _GithubClient
from ethpm_types.source import Compiler, Content, ContractSource
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from ape_vyper._utils import FileType, get_version_pragma_spec, install_vyper, safe_append
from ape_vyper.compiler._versions import (
    BaseVyperCompiler,
    Vyper02Compiler,
    Vyper03Compiler,
    Vyper04Compiler,
)
from ape_vyper.coverage import CoverageProfiler
from ape_vyper.exceptions import VyperCompileError, VyperInstallError, enrich_error
from ape_vyper.flattener import Flattener
from ape_vyper.imports import ImportMap, ImportResolver
from ape_vyper.traceback import SourceTracer

if TYPE_CHECKING:
    from ape.exceptions import ContractLogicError
    from ape.types.coverage import ContractSourceCoverage
    from ape.types.trace import SourceTraceback
    from eth_pydantic_types import HexBytes
    from ethpm_types.contract_type import ContractType


class VyperCompiler(CompilerAPI):
    _dependencies_by_project: dict[str, dict[str, ProjectManager]] = {}

    @property
    def name(self) -> str:
        return "vyper"

    @cached_property
    def vyper_02(self) -> Vyper02Compiler:
        """
        Sub-compiler for Vyper 0.2.7 contracts.
        """
        return Vyper02Compiler(self)

    @cached_property
    def vyper_03(self) -> Vyper03Compiler:
        """
        Sub-compiler for Vyper>=0.3.3,<0.4 contracts.
        """
        return Vyper03Compiler(self)

    @cached_property
    def vyper_04(self) -> Vyper04Compiler:
        """
        Sub-compiler for Vyper>=0.4 contracts.
        """
        return Vyper04Compiler(self)

    @cached_property
    def _import_resolver(self) -> ImportResolver:
        return ImportResolver()

    def get_sub_compiler(self, version: Version) -> BaseVyperCompiler:
        if version < Version("0.3"):
            return self.vyper_02
        elif version < Version("0.4"):
            return self.vyper_03

        return self.vyper_04

    def get_imports(
        self,
        contract_filepaths: Iterable[Path],
        project: Optional[ProjectManager] = None,
    ) -> dict[str, list[str]]:
        pm = project or self.local_project
        imports = self._import_resolver.get_imports(pm, contract_filepaths)
        return {
            f"{get_relative_path(p.absolute(), pm.path)}": [imp.source_id for imp in import_ls]
            for p, import_ls in imports.items()
        }

    def get_versions(self, all_paths: Iterable[Path]) -> set[str]:
        versions = set()
        for path in all_paths:
            if version_spec := get_version_pragma_spec(path):
                try:
                    # Make sure we have the best compiler available to compile this
                    version_iter = version_spec.filter(self.available_versions)

                except VyperInstallError as err:
                    # Possible internet issues. Try to stick to installed versions.
                    logger.error(
                        "Error checking available versions, possibly due to Internet problems. "
                        "Attempting to use the best installed version. "
                        f"Error: {err}"
                    )
                    version_iter = version_spec.filter(self.installed_versions)

                matching_versions = sorted(list(version_iter))
                if matching_versions:
                    versions.add(str(matching_versions[0]))

        return versions

    @cached_property
    def package_version(self) -> Optional[Version]:
        try:
            vyper = import_module("vyper")
        except ModuleNotFoundError:
            return None

        version_str = getattr(vyper, "__version__", None)
        return Version(version_str) if version_str else None

    @cached_property
    def available_versions(self) -> list[Version]:
        # NOTE: Package version should already be included in available versions
        max_retries = 10
        buffer = 1
        times_tried = 0
        result = []
        headers = None
        if token := os.environ.get(_GithubClient.TOKEN_KEY):
            auth = b64encode(token.encode()).decode()
            headers = {"Authorization": f"Basic {auth}"}

        while times_tried < max_retries:
            try:
                result = vvm.get_installable_vyper_versions(headers=headers)
            except ConnectionError as err:
                if "API rate limit exceeded" in str(err):
                    if times_tried == max_retries:
                        raise VyperInstallError(str(err)) from err

                    # Retry
                    logger.warning(
                        f"GitHub throttled requests. Retrying in '{buffer}' seconds. "
                        f"Tries left={max_retries - times_tried}"
                    )
                    time.sleep(buffer)
                    buffer += 1
                    times_tried += 1
                    continue

                else:
                    # This is a different error.
                    raise VyperInstallError(str(err)) from err

            # Succeeded.
            break

        return result

    @property
    def installed_versions(self) -> list[Version]:
        # Doing this so it prefers package version
        package_version = self.package_version
        versions = [package_version] if package_version else []
        # currently package version is [] this should be ok
        return versions + vvm.get_installed_vyper_versions()

    @cached_property
    def vyper_json(self):
        try:
            from vyper.cli import vyper_json  # type: ignore

            return vyper_json
        except ImportError:
            return None

    def get_dependencies(
        self, project: Optional[ProjectManager] = None
    ) -> dict[str, ProjectManager]:
        pm = project or self.local_project
        if pm.project_id in self._dependencies_by_project:
            return self._dependencies_by_project[pm.project_id]

        config = self.get_config(project=pm)
        dependencies: dict[str, ProjectManager] = {}
        handled: set[str] = set()

        # Add remappings from config.
        for remapping in config.import_remapping:
            name = remapping.dependency_name
            if not (_version := remapping.dependency_version):
                versions = pm.dependencies[name]
                if len(versions) == 1:
                    _version = versions[0]
                else:
                    continue

            dependency = pm.dependencies.get_dependency(name, _version)
            dep_id = f"{dependency.name}_{dependency.version}"
            if dep_id in handled or (
                isinstance(dependency.project, LocalProject) and dependency.project.path == pm.path
            ):
                continue

            handled.add(dep_id)
            dependencies[remapping.key] = dependency.project

        # Add auto-remapped dependencies.
        for dependency in pm.dependencies.specified:
            dep_id = f"{dependency.name}_{dependency.version}"
            if dep_id in handled or (
                isinstance(dependency.project, LocalProject) and dependency.project.path == pm.path
            ):
                continue

            handled.add(dep_id)
            dependencies[dependency.name] = dependency.project

        # Cache for next time.
        self._dependencies_by_project[pm.project_id] = dependencies
        return dependencies

    def get_import_remapping(self, project: Optional[ProjectManager] = None) -> dict[str, dict]:
        """
        Configured interface imports from dependencies.
        """
        pm = project or self.local_project
        dependencies = self.get_dependencies(project=pm)
        interfaces: dict[str, dict] = {}
        for key, dependency_project in dependencies.items():
            manifest = dependency_project.manifest
            for name, ct in (manifest.contract_types or {}).items():
                filename = f"{key}/{name}.json"
                abi_list = [x.model_dump(mode="json", by_alias=True) for x in ct.abi]
                interfaces[filename] = {"abi": abi_list}

        return interfaces

    def compile(
        self,
        contract_filepaths: Iterable[Path],
        project: Optional[ProjectManager] = None,
        settings: Optional[dict] = None,
    ) -> Iterator["ContractType"]:
        pm = project or self.local_project
        original_settings = self.compiler_settings
        self.compiler_settings = {**self.compiler_settings, **(settings or {})}
        try:
            yield from self._compile(contract_filepaths, project=pm)
        finally:
            self.compiler_settings = original_settings

    def _compile(
        self, contract_filepaths: Iterable[Path], project: Optional[ProjectManager] = None
    ):
        pm = project or self.local_project
        contract_types: list["ContractType"] = []
        import_map = self._import_resolver.get_imports(pm, contract_filepaths)
        config = self.get_config(pm)
        version_map = self._get_version_map_from_import_map(
            contract_filepaths,
            import_map,
            project=pm,
            config=config,
        )
        compiler_data = self._get_compiler_arguments(version_map, project=pm, config=config)
        all_settings = self._get_compiler_settings_from_version_map(version_map, project=pm)
        contract_versions: dict[str, tuple[Version, str]] = {}

        for vyper_version, version_settings in all_settings.items():
            sub_compiler = self.get_sub_compiler(vyper_version)
            for contract_type, settings_key in sub_compiler.compile(
                vyper_version,
                version_settings,
                import_map,
                compiler_data,
                project=pm,
            ):
                contract_types.append(contract_type)
                contract_versions[contract_type.name] = (vyper_version, settings_key)
                yield contract_type

        # Output compiler data used.
        compilers_used: dict[Version, dict[str, Compiler]] = {}
        for ct in contract_types:
            if not ct.name:
                # Won't happen, but just for mypy.
                continue

            ct_version, ct_settings_key = contract_versions[ct.name]
            settings = all_settings[ct_version][ct_settings_key]

            if ct_version not in compilers_used:
                compilers_used[ct_version] = {}

            if ct_settings_key in compilers_used[ct_version] and ct.name not in (
                compilers_used[ct_version][ct_settings_key].contractTypes or []
            ):
                # Add contractType to already-tracked compiler.
                compilers_used[ct_version][ct_settings_key].contractTypes = [
                    *(compilers_used[ct_version][ct_settings_key].contractTypes or []),
                    ct.name,
                ]

            elif ct_settings_key not in compilers_used[ct_version]:
                # Add optimization-compiler for the first time.
                compilers_used[ct_version][ct_settings_key] = Compiler(
                    name=self.name.lower(),
                    version=f"{ct_version}",
                    contractTypes=[ct.name],
                    settings=settings,
                )

        # Output compiler data to the cached project manifest.
        compilers_ls = [
            compiler
            for optimization_settings in compilers_used.values()
            for compiler in optimization_settings.values()
        ]

        # NOTE: This method handles merging contractTypes and filtered out
        #   no longer used Compilers.
        pm.add_compiler_data(compilers_ls)

    def compile_code(
        self, code: str, project: Optional[ProjectManager] = None, **kwargs
    ) -> "ContractType":
        # NOTE: We are unable to use `vvm.compile_code()` because it does not
        #   appear to honor altered VVM install paths, thus always re-installs
        #   Vyper in our tests because of the monkeypatch. Also, their approach
        #   isn't really different than our approach implemented below.
        pm = project or self.local_project
        with pm.isolate_in_tempdir() as tmp_project:
            name = kwargs.get("contractName", "code")
            file = tmp_project.path / f"{name}.vy"
            file.write_text(code, encoding="utf8")
            contract_type = next(self.compile((file,), project=tmp_project), None)
            if contract_type is None:
                # Not sure when this would happen.
                raise VyperCompileError("Failed to produce contract type.")

            # Clean-up (just in case tmp_project is re-used)
            file.unlink(missing_ok=True)

            return contract_type

    def _source_vyper_version(self, code: str) -> Version:
        """Given source code, figure out which Vyper version to use"""
        version_spec = get_version_pragma_spec(code)

        def first_full_release(versions: Iterable[Version]) -> Optional[Version]:
            for vers in versions:
                if not vers.is_devrelease and not vers.is_postrelease and not vers.is_prerelease:
                    return vers

            return None

        if version_spec is None:
            if version := first_full_release(self.installed_versions + self.available_versions):
                return version

            raise VyperInstallError("No available version.")

        return next(version_spec.filter(self.available_versions))

    def flatten_contract(
        self,
        path: Path,
        project: Optional[ProjectManager] = None,
        **kwargs,
    ) -> Content:
        """
        Returns the flattened contract suitable for compilation or verification as a single file
        """
        pm = project or self.local_project
        flattener = Flattener()
        return flattener.flatten(path, project=pm)

    def get_version_map(
        self,
        contract_filepaths: Iterable[Path],
        project: Optional[ProjectManager] = None,
    ) -> dict[Version, set[Path]]:
        pm = project or self.local_project
        import_map = self._import_resolver.get_imports(pm, contract_filepaths)
        return self._get_version_map_from_import_map(contract_filepaths, import_map, project=pm)

    def _get_version_map_from_import_map(
        self,
        contract_filepaths: Iterable[Path],
        import_map: ImportMap,
        project: Optional[ProjectManager] = None,
        config: Optional[PluginConfig] = None,
    ) -> dict[Version, set[Path]]:
        pm = project or self.local_project
        self.compiler_settings = {**self.compiler_settings}
        config = config or self.get_config(pm)
        version_map: dict[Version, set[Path]] = {}
        source_path_by_version_spec: dict[SpecifierSet, set[Path]] = {}
        source_paths_without_pragma = set()

        # Sort contract_filepaths to promote consistent, reproduce-able behavior
        for path in sorted(contract_filepaths):
            imports = [imp.path for imp in import_map.get(path, []) if imp.path]

            if config_spec := config.version:
                safe_append(source_path_by_version_spec, config_spec, {path, *imports})
            elif pragma := get_version_pragma_spec(path):
                safe_append(source_path_by_version_spec, pragma, {path, *imports})
            else:
                source_paths_without_pragma.add(path)

        # Install all requires versions *before* building map
        for pragma_spec, path_set in source_path_by_version_spec.items():
            if list(pragma_spec.filter(self.installed_versions)):
                # Already met.
                continue

            versions_can_install = sorted(
                list(pragma_spec.filter(self.available_versions)), reverse=True
            )
            if versions_can_install:
                did_install = False
                for version in versions_can_install:
                    if version == self.package_version:
                        break
                    else:
                        install_vyper(version)
                        did_install = True
                        break

                if not did_install:
                    versions_str = ", ".join([f"{v}" for v in versions_can_install])
                    raise VyperInstallError(f"Unable to install vyper version(s) '{versions_str}'.")
            else:
                raise VyperInstallError("No available version to install.")

        # By this point, all the of necessary versions will be installed.
        # Thus, we will select only the best versions to use per source set.
        for pragma_spec, path_set in source_path_by_version_spec.items():
            versions = sorted(list(pragma_spec.filter(self.installed_versions)), reverse=True)
            if versions:
                safe_append(
                    version_map, versions[0], {p for p in path_set if p in contract_filepaths}
                )

        if not self.installed_versions:
            # If we have no installed versions by this point, we need to install one.
            # This happens when there are no pragmas in any sources and no vyper installations.
            install_vyper(max(self.available_versions))

        # Handle no-pragma sources
        if source_paths_without_pragma:
            versions_given = [x for x in version_map.keys()]
            max_installed_vyper_version = None
            if versions_given:
                version_given_non_pre = [x for x in versions_given if not x.pre]
                if version_given_non_pre:
                    max_installed_vyper_version = max(version_given_non_pre)

            if max_installed_vyper_version is None:
                max_installed_vyper_version = max(v for v in self.installed_versions if not v.pre)

            safe_append(
                version_map,
                max_installed_vyper_version,
                {p for p in source_paths_without_pragma if p in contract_filepaths},
            )

        return version_map

    def get_compiler_settings(
        self,
        contract_filepaths: Iterable[Path],
        project: Optional[ProjectManager] = None,
        **kwargs,
    ) -> dict[Version, dict]:
        pm = project or self.local_project
        # NOTE: Interfaces cannot be in the outputSelection
        # (but are required in `sources` for the 0.4.0 range).
        valid_paths = [
            p
            for p in contract_filepaths
            if get_full_extension(p) == FileType.SOURCE
            and not str(p).startswith(str(pm.path / "interfaces"))
        ]
        version_map = self.get_version_map(valid_paths, project=pm)
        return self._get_compiler_settings_from_version_map(version_map, project=pm)

    def _get_compiler_settings_from_version_map(
        self,
        version_map: dict[Version, set[Path]],
        project: Optional[ProjectManager] = None,
    ):
        pm = project or self.local_project
        compiler_data = self._get_compiler_arguments(version_map, project=pm)
        settings = {}
        for version, data in compiler_data.items():
            source_paths = list(version_map.get(version, []))
            if not source_paths:
                continue

            sub_compiler = self.get_sub_compiler(version)
            settings[version] = sub_compiler.get_settings(
                version,
                source_paths,
                data,
                project=pm,
            )

        return settings

    def init_coverage_profile(
        self, source_coverage: "ContractSourceCoverage", contract_source: ContractSource
    ):
        profiler = CoverageProfiler(source_coverage)
        profiler.initialize(contract_source)

    def enrich_error(self, err: "ContractLogicError") -> "ContractLogicError":
        return enrich_error(err)

    # TODO: In 0.9, make sure project is a kwarg here.
    def trace_source(
        self, contract_source: ContractSource, trace: TraceAPI, calldata: "HexBytes"
    ) -> "SourceTraceback":
        return SourceTracer.trace(trace.get_raw_frames(), contract_source, calldata)

    def _get_compiler_arguments(
        self,
        version_map: dict,
        project: Optional[ProjectManager] = None,
        config: Optional[PluginConfig] = None,
    ) -> dict[Version, dict]:
        pm = project or self.local_project
        config = config or self.get_config(pm)
        evm_version = config.evm_version
        arguments_map = {}
        for vyper_version, source_paths in version_map.items():
            bin_arg = self._get_vyper_bin(vyper_version)
            arguments_map[vyper_version] = {
                "base_path": f"{pm.path}",
                "evm_version": evm_version,
                "vyper_version": str(vyper_version),
                "vyper_binary": bin_arg,
            }

        return arguments_map

    def _get_vyper_bin(self, vyper_version: Version):
        return shutil.which("vyper") if vyper_version is self.package_version else None
