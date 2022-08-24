import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Set, Union

import vvm  # type: ignore
from ape.api import ConfigDict
from ape.api.compiler import CompilerAPI
from ape.types import ContractType
from ape.utils import cached_property, get_relative_path
from semantic_version import NpmSpec, Version  # type: ignore

from .exceptions import VyperCompileError, VyperInstallError


def _install_vyper(version: Version):
    try:
        vvm.install_vyper(version, show_progress=True)
    except Exception as err:
        raise VyperInstallError(f"Unable to install Vyper version: '{version}'.") from err


def get_pragma_spec(source: str) -> Optional[NpmSpec]:
    """
    Extracts pragma information from Vyper source code.

    Args:
        source: Vyper source code

    Returns:
        NpmSpec object or None, if no valid pragma is found
    """
    pragma_match = next(re.finditer(r"(?:\n|^)\s*#\s*@version\s*([^\n]*)", source), None)
    if pragma_match is None:
        return None  # Try compiling with latest

    pragma_string = pragma_match.groups()[0]
    pragma_string = " ".join(pragma_string.split())

    try:
        return NpmSpec(pragma_string)

    except ValueError:
        return None


class VyperCompiler(CompilerAPI):
    config: ConfigDict = ConfigDict()

    @property
    def name(self) -> str:
        return "vyper"

    def get_versions(self, all_paths: List[Path]) -> Set[str]:
        versions = set()
        for path in all_paths:
            source = path.read_text()

            # Make sure we have the compiler available to compile this
            version_spec = get_pragma_spec(source)
            if version_spec:
                versions.add(str(version_spec.select(self.available_versions)))

        return versions

    @cached_property
    def package_version(self) -> Optional[Version]:
        try:
            import vyper  # type: ignore

            # Strip off parts from source-installation
            version = Version.coerce(vyper.__version__)
            return Version(major=version.major, minor=version.minor, patch=version.patch)

        except ImportError:
            return None

    @cached_property
    def available_versions(self) -> List[Version]:
        # NOTE: Package version should already be included in available versions
        return vvm.get_installable_vyper_versions()

    @property
    def installed_versions(self) -> List[Version]:
        # Doing this so it prefers package version
        package_version = self.package_version
        package_version = [package_version] if package_version else []
        # currently package version is [] this should be ok
        return package_version + vvm.get_installed_vyper_versions()

    @cached_property
    def vyper_json(self):
        from vyper.cli import vyper_json  # type: ignore

        return vyper_json

    def compile(
        self, contract_filepaths: List[Path], base_path: Optional[Path] = None
    ) -> List[ContractType]:
        contract_types = []
        base_folder = base_path or self.config_manager.contracts_folder
        version_map = self.get_version_map(contract_filepaths)

        for vyper_version, source_paths in version_map.items():
            for path in source_paths:
                vyper_binary = (
                    shutil.which("vyper") if vyper_version is self.package_version else None
                )
                try:
                    result = vvm.compile_source(
                        path.read_text(),
                        base_path=self.project_manager.interfaces_folder,
                        vyper_version=vyper_version,
                        vyper_binary=vyper_binary,
                    )["<stdin>"]
                except Exception as err:
                    raise VyperCompileError(err) from err

                contract_path = (
                    str(get_relative_path(path, base_folder))
                    if base_folder and path.is_absolute()
                    else str(path)
                )

                # NOTE: Vyper doesn't have internal contract type declarations, use filename
                result["contractName"] = Path(contract_path).stem
                result["sourceId"] = contract_path
                result["deploymentBytecode"] = {"bytecode": result["bytecode"]}
                result["runtimeBytecode"] = {"bytecode": result["bytecode_runtime"]}
                contract_types.append(ContractType.parse_obj(result))

        return contract_types

    def get_version_map(
        self, contract_filepaths: List[Path], base_path: Optional[Path] = None
    ) -> Dict[Version, Set[Path]]:
        version_map: Dict[Version, Set[Path]] = {}
        source_path_by_pragma_spec: Dict[NpmSpec, Set[Path]] = {}
        source_paths_without_pragma = set()
        for path in contract_filepaths:
            pragma_spec = get_pragma_spec(path.read_text())
            if not pragma_spec:
                source_paths_without_pragma.add(path)
            else:
                _safe_append(source_path_by_pragma_spec, pragma_spec, path)

        for pragma_spec, path_set in source_path_by_pragma_spec.items():
            installed_compatible_version = pragma_spec.select(self.installed_versions)
            if installed_compatible_version:
                _safe_append(version_map, installed_compatible_version, path_set)
                continue

            # Check if we need to install specified compiler version
            available_vyper_version = pragma_spec.select(self.available_versions)
            if available_vyper_version and available_vyper_version != self.package_version:
                _install_vyper(available_vyper_version)
                _safe_append(version_map, available_vyper_version, path_set)
            elif available_vyper_version:
                raise VyperInstallError(
                    f"Unable to install vyper version '{available_vyper_version}'."
                )
            else:
                raise VyperInstallError("No available version to install.")

        if not self.installed_versions:
            # If we have no installed versions by this point, we need to install one.
            # This happens when there are no pragmas in any sources and no vyper installations.
            _install_vyper(max(self.available_versions))

        # Handle no-pragma sources
        max_installed_vyper_version = max(self.installed_versions)
        _safe_append(version_map, max_installed_vyper_version, source_paths_without_pragma)
        return version_map


def _safe_append(data: Dict, version: Union[Version, NpmSpec], paths: Union[Path, Set]):
    if isinstance(paths, Path):
        paths = {paths}
    if version in data:
        data[version] = data[version].union(paths)
    else:
        data[version] = paths
