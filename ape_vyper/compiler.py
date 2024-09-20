import os
import re
import shutil
import time
from base64 import b64encode
from collections import defaultdict
from collections.abc import Iterable, Iterator
from fnmatch import fnmatch
from importlib import import_module
from pathlib import Path
from site import getsitepackages
from typing import Any, Optional, Union, cast

import vvm  # type: ignore
from ape.api import PluginConfig, TraceAPI
from ape.api.compiler import CompilerAPI
from ape.exceptions import ContractLogicError
from ape.logging import LogLevel, logger
from ape.managers.project import LocalProject, ProjectManager
from ape.types import ContractSourceCoverage, ContractType, SourceTraceback
from ape.utils import ManagerAccessMixin, cached_property, get_relative_path
from ape.utils._github import _GithubClient
from ape.utils.os import clean_path, get_full_extension
from eth_pydantic_types import HexBytes
from eth_utils import is_0x_prefixed
from ethpm_types import ASTNode, PackageManifest, PCMap, SourceMapItem
from ethpm_types.ast import ASTClassification
from ethpm_types.contract_type import SourceMap
from ethpm_types.source import Compiler, Content, ContractSource, Function, SourceLocation
from evm_trace import TraceFrame
from evm_trace.enums import CALL_OPCODES
from evm_trace.geth import create_call_node_data
from packaging.specifiers import SpecifierSet
from packaging.version import Version
from vvm import compile_standard as vvm_compile_standard
from vvm.exceptions import VyperError  # type: ignore

from ape_vyper._utils import (
    EVM_VERSION_DEFAULT,
    FileType,
    Optimization,
    get_evm_version_pragma_map,
    get_optimization_pragma_map,
    get_version_pragma_spec,
    install_vyper,
    lookup_source_from_site_packages,
)
from ape_vyper.ast import source_to_abi
from ape_vyper.exceptions import (
    RUNTIME_ERROR_MAP,
    IntegerBoundsCheck,
    RuntimeErrorType,
    VyperCompileError,
    VyperInstallError,
)
from ape_vyper.interface import (
    extract_import_aliases,
    extract_imports,
    extract_meta,
    generate_interface,
)

DEV_MSG_PATTERN = re.compile(r".*\s*#\s*(dev:.+)")
_RETURN_OPCODES = ("RETURN", "REVERT", "STOP")
_FUNCTION_DEF = "FunctionDef"
_FUNCTION_AST_TYPES = (_FUNCTION_DEF, "Name", "arguments")
_EMPTY_REVERT_OFFSET = 18
_NON_PAYABLE_STR = f"dev: {RuntimeErrorType.NONPAYABLE_CHECK.value}"


class BaseVyperCompiler(ManagerAccessMixin):
    """
    Shared logic between all versions of Vyper.
    """

    def get_sources_dictionary(
        self, source_ids: Iterable[str], project: Optional[ProjectManager] = None, **kwargs
    ) -> dict[str, dict]:
        """
        Get the sources dictionary for Vyper's input JSON. All Vyper versions < 0.4
        **MUST NOT** include interfaces in the sources dictionary.
        """
        pm = project or self.local_project
        return {
            s: {"content": p.read_text(encoding="utf8")}
            for s, p in {src_id: pm.path / src_id for src_id in source_ids}.items()
            if p.parent != pm.path / "interfaces"
        }

    def get_selection_dictionary(
        self,
        selection: Iterable[str],
        project: Optional[ProjectManager] = None,
        **kwargs,
    ) -> dict:
        pm = project or self.local_project
        return {s: ["*"] for s in selection if (pm.path / s).is_file() if "interfaces" not in s}

    def get_compile_kwargs(
        self, vyper_version: Version, compiler_data: dict, project: Optional[ProjectManager] = None
    ) -> dict:
        pm = project or self.local_project
        comp_kwargs = self._get_base_compile_kwargs(vyper_version, compiler_data)
        # `base_path` is required for pre-0.4 versions or else imports won't resolve.
        comp_kwargs["base_path"] = pm.path
        return comp_kwargs

    def _get_base_compile_kwargs(self, vyper_version: Version, compiler_data: dict):
        vyper_binary = compiler_data[vyper_version]["vyper_binary"]
        comp_kwargs = {"vyper_version": vyper_version, "vyper_binary": vyper_binary}
        return comp_kwargs

    def get_pcmap(
        self,
        vyper_version: Version,
        ast: Any,
        src_map: list,
        opcodes: list[str],
        bytecode: dict,
    ):
        return _get_pcmap(bytecode)

    def parse_source_map(self, raw_source_map: Any) -> SourceMap:
        # All versions < 0.4 use this one
        return SourceMap(root=raw_source_map)

    def get_default_optimization(self, vyper_version: Version) -> Optimization:
        return True


class Vyper02Compiler(BaseVyperCompiler):
    """
    Compiler for Vyper>=0.2.7,<0.3.
    """

    DEFAULT_OPTIMIZATION = True

    def get_pcmap(
        self,
        vyper_version: Version,
        ast: Any,
        src_map: list,
        opcodes: list[str],
        bytecode: dict,
    ):
        return _get_legacy_pcmap(ast, src_map, opcodes)


class Vyper03Compiler(BaseVyperCompiler):
    """
    Compiler for Vyper>=0.3.3,<0.4.
    """

    def get_pcmap(
        self, vyper_version: Version, ast: Any, src_map: list, opcodes: list[str], bytecode: dict
    ):
        return (
            _get_legacy_pcmap(ast, src_map, opcodes)
            if vyper_version <= Version("0.3.7")
            else _get_pcmap(bytecode)
        )

    def get_default_optimization(self, vyper_version: Version) -> Optimization:
        return True if vyper_version < Version("0.3.10") else "gas"

    def get_selection_dictionary(
        self, selection: Iterable[str], project: Optional[ProjectManager] = None, **kwargs
    ) -> dict:
        pm = project or self.local_project
        use_absolute_paths = kwargs.get("use_absolute_paths", False)

        def _to_src_id(s):
            return str(pm.path / s) if use_absolute_paths else s

        return {
            _to_src_id(s): ["*"]
            for s in selection
            if ((pm.path / s).is_file() if use_absolute_paths else Path(s).is_file())
            and f"interfaces{os.path.sep}" not in s
            and get_full_extension(pm.path / s) != FileType.INTERFACE
        }


class Vyper04Compiler(BaseVyperCompiler):
    """
    Compiler for Vyper>=0.4.0.
    """

    def get_sources_dictionary(
        self, source_ids: Iterable[str], project: Optional[ProjectManager] = None, **kwargs
    ) -> dict[str, dict]:
        pm = project or self.local_project
        if not source_ids:
            return {}

        use_absolute_paths = kwargs.get("use_absolute_path", False)
        import_map = kwargs.get("import_map", {})
        if use_absolute_paths:
            # Dependencies and testing.
            src_dict = {
                str(pm.path / src_id): {"content": (pm.path / src_id).read_text(encoding="utf8")}
                for src_id in source_ids
            }
        else:
            src_dict = {p: {"content": Path(p).read_text(encoding="utf8")} for p in source_ids}

        for src in source_ids:
            if Path(src).is_absolute():
                src_id = (
                    str(Path(src))
                    if use_absolute_paths
                    else f"{get_relative_path(Path(src), pm.path)}"
                )
            else:
                src_id = src

            if imports := import_map.get(src_id):
                for imp in imports:
                    if imp in src_dict:
                        continue

                    imp_path = Path(imp)
                    abs_import = Path(imp).is_absolute()

                    if not abs_import and (pm.path / imp).is_file():
                        # Is a local file.
                        imp_path = pm.path / imp
                        if not imp_path.is_file():
                            continue

                        src_dict[imp] = {"content": imp_path.read_text(encoding="utf8")}

                    elif not abs_import:
                        # Is from a dependency.
                        specified = {d.name: d for d in pm.dependencies.specified}
                        for parent in imp_path.parents:
                            if parent.name == "site-packages":
                                src_id = f"{get_relative_path(imp_path, parent)}"
                                break

                            elif parent.name in specified:
                                dependency = specified[parent.name]
                                src_id = f"{imp_path}"
                                imp_path = dependency.project.path / imp_path
                                if imp_path.is_file():
                                    break

                        # Likely from a dependency. Exclude absolute prefixes so Vyper
                        # knows what to do.
                        if imp_path.is_file() and not Path(src_id).is_absolute():
                            src_dict[src_id] = {"content": imp_path.read_text(encoding="utf8")}

        return src_dict

    def get_compile_kwargs(
        self, vyper_version: Version, compiler_data: dict, project: Optional[ProjectManager] = None
    ) -> dict:
        return self._get_base_compile_kwargs(vyper_version, compiler_data)

    def get_default_optimization(self, vyper_version: Version) -> Optimization:
        return "gas"

    def parse_source_map(self, raw_source_map: dict) -> SourceMap:
        return SourceMap(root=raw_source_map["pc_pos_map_compressed"])


class VyperCompiler(CompilerAPI):
    @property
    def name(self) -> str:
        return "vyper"

    @cached_property
    def vyper_02(self) -> Vyper02Compiler:
        """
        Sub-compiler for Vyper 0.2.7 contracts.
        """
        return Vyper02Compiler()

    @cached_property
    def vyper_03(self) -> Vyper03Compiler:
        """
        Sub-compiler for Vyper>=0.3.3,<0.4 contracts.
        """
        return Vyper03Compiler()

    @cached_property
    def vyper_04(self) -> Vyper04Compiler:
        """
        Sub-compiler for Vyper>=0.4 contracts.
        """
        return Vyper04Compiler()

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
        return self._get_imports(contract_filepaths, project=pm, handled=set())

    def _get_imports(
        self,
        contract_filepaths: Iterable[Path],
        project: Optional[ProjectManager] = None,
        handled: Optional[set[str]] = None,
        use_absolute_paths: Optional[None] = None,
    ):
        pm = project or self.local_project

        if use_absolute_paths is None:
            # When compiling projects outside the cwd, we must
            # use absolute paths.
            use_absolute_paths = pm.path != Path.cwd()

        import_map: defaultdict = defaultdict(list)
        handled = handled or set()
        dependencies = self.get_dependencies(project=pm)
        for path in contract_filepaths:
            if not path.is_file():
                continue

            content = path.read_text(encoding="utf8").splitlines()
            source_id = (
                str(path.absolute())
                if use_absolute_paths
                else str(get_relative_path(path.absolute(), pm.path.absolute()))
            )

            # Prevent infinitely handling imports when they cross over.
            if source_id in handled:
                continue

            handled.add(source_id)
            for line in content:
                if line.startswith("import "):
                    import_line_parts = line.replace("import ", "").split(" ")
                    prefix = import_line_parts[0]

                elif line.startswith("from ") and " import " in line:
                    import_line_parts = line.replace("from ", "").strip().split(" ")
                    module_name = import_line_parts[0].strip()
                    prefix = os.path.sep.join([module_name, import_line_parts[2].strip()])

                else:
                    # Not an import line
                    continue

                dots = ""
                while prefix.startswith("."):
                    dots += prefix[0]
                    prefix = prefix[1:]

                is_relative: Optional[bool] = None
                if dots != "":
                    is_relative = True
                # else: we are unsure since dots are not required.

                # Replace rest of dots with slashes.
                prefix = prefix.replace(".", os.path.sep)

                if prefix.startswith("vyper/") or prefix.startswith("ethereum/"):
                    if f"{prefix}.json" not in import_map[source_id]:
                        import_map[source_id].append(f"{prefix}.json")

                    continue

                relative_path = None
                abs_path = None
                if is_relative is True:
                    relative_path = (path.parent / dots / prefix.lstrip(os.path.sep)).resolve()
                elif is_relative is False:
                    abs_path = (pm.path / prefix.lstrip(os.path.sep)).resolve()
                elif is_relative is None:
                    relative_path = (path.parent / dots / prefix.lstrip(os.path.sep)).resolve()
                    abs_path = (pm.path / prefix.lstrip(os.path.sep)).resolve()

                local_prefix_relative = (
                    None
                    if relative_path is None
                    else str(relative_path).replace(f"{pm.path}", "").lstrip(os.path.sep)
                )
                local_prefix_abs = (
                    None
                    if abs_path is None
                    else str(abs_path).replace(f"{pm.path}", "").lstrip(os.path.sep)
                )

                import_source_id = None
                is_local = True
                local_path = None  # TBD
                local_prefix = None  # TBD

                if (pm.path / f"{local_prefix_relative}{FileType.SOURCE}").is_file():
                    # Relative source.
                    ext = FileType.SOURCE.value
                    local_path = relative_path
                    local_prefix = local_prefix_relative

                elif (pm.path / f"{local_prefix_relative}{FileType.INTERFACE}").is_file():
                    # Relative interface.
                    ext = FileType.INTERFACE.value
                    local_path = relative_path
                    local_prefix = local_prefix_relative

                elif (pm.path / f"{local_prefix_relative}.json").is_file():
                    # Relative JSON interface.
                    ext = ".json"
                    local_path = relative_path
                    local_prefix = local_prefix_relative

                elif (pm.path / f"{local_prefix_abs}{FileType.SOURCE}").is_file():
                    # Absolute source.
                    ext = FileType.SOURCE.value
                    local_path = abs_path
                    local_prefix = local_prefix_abs

                elif (pm.path / f"{local_prefix_abs}{FileType.INTERFACE}").is_file():
                    # Absolute interface.
                    ext = FileType.INTERFACE.value
                    local_path = abs_path
                    local_prefix = local_prefix_abs

                elif (pm.path / f"{local_prefix_abs}.json").is_file():
                    # Absolute JSON interface.
                    ext = ".json"
                    local_path = abs_path
                    local_prefix = local_prefix_abs

                else:
                    # Must be an interface JSON specified in the input JSON.
                    ext = ".json"
                    dep_key = prefix.split(os.path.sep)[0]
                    dependency_name = prefix.split(os.path.sep)[0]
                    filestem = prefix.replace(f"{dependency_name}{os.path.sep}", "")
                    found = False
                    if dependency_name:
                        # Attempt looking up dependency from site-packages.
                        if res := lookup_source_from_site_packages(dependency_name, filestem):
                            source_path, imported_project = res
                            import_source_id = str(source_path)
                            # Also include imports of imports.
                            sub_imports = self._get_imports(
                                (source_path,),
                                project=imported_project,
                                handled=handled,
                                use_absolute_paths=use_absolute_paths,
                            )
                            for sub_import_ls in sub_imports.values():
                                import_map[source_id].extend(sub_import_ls)

                            is_local = False
                            found = True

                    if not found and dep_key in dependencies:
                        for version_str, dep_project in pm.dependencies[dependency_name].items():
                            dependency = pm.dependencies.get_dependency(
                                dependency_name, version_str
                            )
                            contracts_path = dep_project.contracts_folder
                            dependency_source_prefix = (
                                f"{get_relative_path(contracts_path, dep_project.path)}"
                            )
                            source_id_stem = (
                                f"{dependency_source_prefix}{os.path.sep}{filestem}".lstrip(
                                    f"{os.path.sep}."
                                )
                            )
                            for ext in (".vy", ".vyi", ".json"):
                                if f"{source_id_stem}{ext}" not in dep_project.sources:
                                    continue

                                # Dependency located.
                                if not dependency.project.manifest.contract_types:
                                    # In this case, the dependency *must* be compiled
                                    # so the ABIs can be found later on.
                                    with logger.at_level(LogLevel.ERROR):
                                        try:
                                            dependency.compile()
                                        except Exception as err:
                                            # Compiling failed. Try to continue anyway to get
                                            # a better error from the Vyper compiler, in case
                                            # something else is wrong.
                                            logger.warning(
                                                f"Failed to compile dependency '{dependency.name}' "
                                                f"@ '{dependency.version}'.\n"
                                                f"Reason: {err}"
                                            )

                                import_source_id = f"{source_id_stem}{ext}"
                                import_path = dep_project.path / f"{source_id_stem}{ext}"

                                # Also include imports of imports.
                                sub_imports = self._get_imports(
                                    (import_path,),
                                    project=dep_project,
                                    handled=handled,
                                    use_absolute_paths=use_absolute_paths,
                                )
                                for sub_import_ls in sub_imports.values():
                                    import_map[source_id].extend(sub_import_ls)

                                is_local = False
                                break

                    elif not found:
                        logger.error(
                            f"'{dependency_name}' may not be installed. "
                            "Could not find it in Ape dependencies or Python's site-packages."
                        )

                if is_local and local_prefix is not None and local_path is not None:
                    import_source_id = f"{local_prefix}{ext}"
                    full_path = local_path.parent / f"{local_path.stem}{ext}"

                    # Also include imports of imports.
                    sub_imports = self._get_imports(
                        (full_path,),
                        project=pm,
                        handled=handled,
                        use_absolute_paths=use_absolute_paths,
                    )

                    for sub_import_ls in sub_imports.values():
                        import_map[source_id].extend(sub_import_ls)

                    if use_absolute_paths:
                        import_source_id = str(full_path)

                if import_source_id and import_source_id not in import_map[source_id]:
                    import_map[source_id].append(import_source_id)

        return dict(import_map)

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

    def classify_ast(self, _node: ASTNode):
        if _node.ast_type in _FUNCTION_AST_TYPES:
            _node.classification = ASTClassification.FUNCTION

        for child in _node.children:
            self.classify_ast(child)

    def compile(
        self,
        contract_filepaths: Iterable[Path],
        project: Optional[ProjectManager] = None,
        settings: Optional[dict] = None,
    ) -> Iterator[ContractType]:
        pm = project or self.local_project

        # (0.4.0): If compiling a project outside the cwd (such as a dependency),
        # we are forced to use absolute paths.
        use_absolute_paths = pm.path != Path.cwd()

        self.compiler_settings = {**self.compiler_settings, **(settings or {})}
        contract_types: list[ContractType] = []
        import_map = self.get_imports(contract_filepaths, project=pm)
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
        import_remapping = self.get_import_remapping(project=pm)

        for vyper_version, version_settings in all_settings.items():
            for settings_key, settings_set in version_settings.items():
                sub_compiler = self.get_sub_compiler(vyper_version)
                src_dict = sub_compiler.get_sources_dictionary(
                    settings_set["outputSelection"],
                    project=pm,
                    use_absolute_paths=use_absolute_paths,
                    import_map=import_map,
                )

                input_json: dict = {
                    "language": "Vyper",
                    "settings": settings_set,
                    "sources": src_dict,
                }

                if interfaces := import_remapping:
                    input_json["interfaces"] = interfaces

                # Output compiler details.
                keys = (
                    "\n\t".join(
                        sorted(
                            [
                                clean_path(Path(x))
                                for x in settings_set.get("outputSelection", {}).keys()
                            ]
                        )
                    )
                    or "No input."
                )
                log_str = f"Compiling using Vyper compiler '{vyper_version}'.\nInput:\n\t{keys}"
                logger.info(log_str)
                comp_kwargs = sub_compiler.get_compile_kwargs(
                    vyper_version, compiler_data, project=pm
                )
                try:
                    result = vvm_compile_standard(input_json, **comp_kwargs)
                except VyperError as err:
                    raise VyperCompileError(err) from err

                for source_id, output_items in result["contracts"].items():
                    content = {
                        i + 1: ln
                        for i, ln in enumerate(
                            (pm.path / source_id).read_text(encoding="utf8").splitlines()
                        )
                    }
                    for name, output in output_items.items():
                        # De-compress source map to get PC POS map.
                        ast = ASTNode.model_validate(result["sources"][source_id]["ast"])
                        self.classify_ast(ast)

                        # Track function offsets.
                        function_offsets = []
                        for node in ast.children:
                            lineno = node.lineno

                            # NOTE: Constructor is handled elsewhere.
                            if node.ast_type == "FunctionDef" and "__init__" not in content.get(
                                lineno, ""
                            ):
                                function_offsets.append((node.lineno, node.end_lineno))

                        evm = output["evm"]
                        bytecode = evm["deployedBytecode"]
                        opcodes = bytecode["opcodes"].split(" ")
                        compressed_src_map = sub_compiler.parse_source_map(bytecode["sourceMap"])
                        src_map = list(compressed_src_map.parse())[1:]
                        pcmap = sub_compiler.get_pcmap(
                            vyper_version, ast, src_map, opcodes, bytecode
                        )

                        # Find content-specified dev messages.
                        dev_messages = {}
                        for line_no, line in content.items():
                            if match := re.search(DEV_MSG_PATTERN, line):
                                dev_messages[line_no] = match.group(1).strip()

                        source_id_path = Path(source_id)
                        if source_id_path.is_absolute():
                            final_source_id = f"{get_relative_path(Path(source_id), pm.path)}"
                        else:
                            final_source_id = source_id

                        contract_type = ContractType.model_validate(
                            {
                                "ast": ast,
                                "contractName": name,
                                "sourceId": final_source_id,
                                "deploymentBytecode": {"bytecode": evm["bytecode"]["object"]},
                                "runtimeBytecode": {"bytecode": bytecode["object"]},
                                "abi": output["abi"],
                                "sourcemap": compressed_src_map,
                                "pcmap": pcmap,
                                "userdoc": output["userdoc"],
                                "devdoc": output["devdoc"],
                                "dev_messages": dev_messages,
                            }
                        )
                        contract_types.append(contract_type)
                        contract_versions[name] = (vyper_version, settings_key)
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
    ) -> ContractType:
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

    def _flatten_source(
        self,
        path: Path,
        project: Optional[ProjectManager] = None,
        include_pragma: bool = True,
        sources_handled: Optional[set[Path]] = None,
        warn_flattening_modules: bool = True,
    ) -> str:
        pm = project or self.local_project
        handled = sources_handled or set()
        handled.add(path)
        # Get the non stdlib import paths for our contracts
        imports = list(
            filter(
                lambda x: not x.startswith("vyper/"),
                [y for x in self.get_imports((path,), project=pm).values() for y in x],
            )
        )

        dependencies: dict[str, PackageManifest] = {}
        dependency_projects = self.get_dependencies(project=pm)
        for key, dependency_project in dependency_projects.items():
            package = key.split("=")[0]
            base = dependency_project.path if hasattr(dependency_project, "path") else package
            manifest = dependency_project.manifest
            if manifest.sources is None:
                continue

            for source_id in manifest.sources.keys():
                import_match = f"{base}/{source_id}"
                dependencies[import_match] = manifest

        interfaces_source = ""
        og_source = (pm.path / path).read_text(encoding="utf8")

        # Get info about imports and source meta
        aliases = extract_import_aliases(og_source)
        pragma, source_without_meta = extract_meta(og_source)
        version_specifier = get_version_pragma_spec(pragma) if pragma else None
        stdlib_imports, _, source_without_imports = extract_imports(source_without_meta)
        flattened_modules = ""
        modules_prefixes: set[str] = set()

        for import_path in sorted(imports):
            import_file = None
            for base in (pm.path, pm.interfaces_folder):
                for opt in {import_path, import_path.replace(f"interfaces{os.path.sep}", "")}:
                    try_import_file = base / opt
                    if try_import_file.is_file():
                        import_file = try_import_file
                        break

            if import_file is None:
                import_file = pm.path / import_path

            # Vyper imported interface names come from their file names
            file_name = import_file.stem
            # If we have a known alias, ("import X as Y"), use the alias as interface name
            iface_name = aliases[file_name] if file_name in aliases else file_name

            def _match_source(imp_path: str) -> Optional[PackageManifest]:
                for source_path in dependencies.keys():
                    if source_path.endswith(imp_path):
                        return dependencies[source_path]

                return None

            if matched_source := _match_source(import_path):
                if not matched_source.contract_types:
                    continue

                abis = [
                    el
                    for k in matched_source.contract_types.keys()
                    for el in matched_source.contract_types[k].abi
                ]
                interfaces_source += generate_interface(abis, iface_name)
                continue

            # Generate an ABI from the source code
            elif import_file.is_file():
                if (
                    version_specifier
                    and version_specifier.contains("0.4.0")
                    and import_file.suffix != ".vyi"
                ):
                    if warn_flattening_modules:
                        logger.warning(
                            "Flattening modules DOES NOT yield the same bytecode! "
                            "This is **NOT** valid for contract-verification."
                        )
                        warn_flattening_modules = False

                    modules_prefixes.add(import_file.stem)
                    if import_file in handled:
                        # We have already included this source somewhere.
                        continue

                    # Is a module or an interface imported from a module.
                    # Copy in the source code directly.
                    flattened_module = self._flatten_source(
                        import_file,
                        include_pragma=False,
                        sources_handled=handled,
                        warn_flattening_modules=warn_flattening_modules,
                    )
                    flattened_modules = f"{flattened_modules}\n\n{flattened_module}"

                else:
                    # Vyper <0.4 interface from folder other than interfaces/
                    # such as a .vyi file in the contracts folder.
                    abis = source_to_abi(import_file.read_text(encoding="utf8"))
                    interfaces_source += generate_interface(abis, iface_name)

        def no_nones(it: Iterable[Optional[str]]) -> Iterable[str]:
            # Type guard like generator to remove Nones and make mypy happy
            for el in it:
                if el is not None:
                    yield el

        pragma_to_include = pragma if include_pragma else ""

        # Join all the OG and generated parts back together
        flattened_source = "\n\n".join(
            no_nones(
                (
                    pragma_to_include,
                    stdlib_imports,
                    interfaces_source,
                    flattened_modules,
                    source_without_imports,
                )
            )
        )

        # Clear module-usage prefixes.
        for prefix in modules_prefixes:
            # Replace usage lines like 'zero_four_module.moduleMethod()'
            # with 'self.moduleMethod()'.
            flattened_source = flattened_source.replace(f"{prefix}.", "self.")

        # Remove module-level doc-strings, as it causes compilation issues
        # when used in root contracts.
        lines_no_doc: list[str] = []
        in_str_comment = False
        for line in flattened_source.splitlines():
            line_stripped = line.rstrip()
            if not in_str_comment and line_stripped.startswith('"""'):
                if line_stripped == '"""' or not line_stripped.endswith('"""'):
                    in_str_comment = True
                continue

            elif in_str_comment:
                if line_stripped.endswith('"""'):
                    in_str_comment = False

                continue

            lines_no_doc.append(line)

        flattened_source = "\n".join(lines_no_doc)

        # TODO: Replace this nonsense with a real code formatter
        def format_source(source: str) -> str:
            while "\n\n\n\n" in source:
                source = source.replace("\n\n\n\n", "\n\n\n")
            return source

        return format_source(flattened_source)

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
        src = self._flatten_source(path, project=pm)
        return Content({i: ln for i, ln in enumerate(src.splitlines())})

    def get_version_map(
        self,
        contract_filepaths: Iterable[Path],
        project: Optional[ProjectManager] = None,
    ) -> dict[Version, set[Path]]:
        pm = project or self.local_project
        import_map = self.get_imports(contract_filepaths, project=pm)
        return self._get_version_map_from_import_map(contract_filepaths, import_map, project=pm)

    def _get_version_map_from_import_map(
        self,
        contract_filepaths: Iterable[Path],
        import_map: dict[str, list[str]],
        project: Optional[ProjectManager] = None,
        config: Optional[PluginConfig] = None,
    ):
        pm = project or self.local_project
        self.compiler_settings = {**self.compiler_settings}
        config = config or self.get_config(pm)
        version_map: dict[Version, set[Path]] = {}
        source_path_by_version_spec: dict[SpecifierSet, set[Path]] = {}
        source_paths_without_pragma = set()

        # Sort contract_filepaths to promote consistent, reproduce-able behavior
        for path in sorted(contract_filepaths):
            src_id = f"{get_relative_path(path.absolute(), pm.path)}"
            imports = [pm.path / imp for imp in import_map.get(src_id, [])]

            if config_spec := config.version:
                _safe_append(source_path_by_version_spec, config_spec, {path, *imports})
            elif pragma := get_version_pragma_spec(path):
                _safe_append(source_path_by_version_spec, pragma, {path, *imports})
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
                _safe_append(
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

            _safe_append(
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

        # When compiling projects outside the cwd, use absolute paths for ease.
        # Also, struggled to get it work any other way.
        use_absolute_paths = pm.path != Path.cwd()

        compiler_data = self._get_compiler_arguments(version_map, project=pm)
        settings = {}
        for version, data in compiler_data.items():
            sub_compiler = self.get_sub_compiler(version)
            source_paths = list(version_map.get(version, []))
            if not source_paths:
                continue

            default_optimization = sub_compiler.get_default_optimization(version)
            output_selection: dict[str, set[str]] = {}
            optimizations_map = get_optimization_pragma_map(
                source_paths, pm.path, default_optimization
            )
            evm_version_map = get_evm_version_pragma_map(source_paths, pm.path)
            default_evm_version = data.get(
                "evm_version", data.get("evmVersion")
            ) or EVM_VERSION_DEFAULT.get(version.base_version)
            for source_path in source_paths:
                source_id = str(get_relative_path(source_path.absolute(), pm.path))

                if not (optimization := optimizations_map.get(source_id)):
                    optimization = sub_compiler.get_default_optimization(version)

                evm_version = evm_version_map.get(source_id, default_evm_version)
                settings_key = f"{optimization}%{evm_version}".lower()
                if settings_key not in output_selection:
                    output_selection[settings_key] = {source_id}
                else:
                    output_selection[settings_key].add(source_id)

            version_settings: dict[str, dict] = {}
            for settings_key, selection in output_selection.items():
                optimization, evm_version = settings_key.split("%")
                if optimization == "true":
                    optimization = True
                elif optimization == "false":
                    optimization = False

                selection_dict = sub_compiler.get_selection_dictionary(
                    selection, use_absolute_paths=use_absolute_paths
                )
                search_paths = [*getsitepackages()]
                if pm.path == Path.cwd():
                    search_paths.append(".")
                else:
                    search_paths.append(str(pm.path))
                    # search_paths.append(str(pm.contracts_folder.parent))
                # else: only seem to get absolute paths to work (for compiling deps alone).

                version_settings[settings_key] = {
                    "optimize": optimization,
                    "outputSelection": selection_dict,
                    "search_paths": search_paths,
                }
                if evm_version and evm_version not in ("none", "null"):
                    version_settings[settings_key]["evmVersion"] = f"{evm_version}"

            settings[version] = version_settings

        return settings

    def init_coverage_profile(
        self, source_coverage: ContractSourceCoverage, contract_source: ContractSource
    ):
        exclusions = self.config_manager.get_config("test").coverage.exclude
        contract_name = contract_source.contract_type.name or "__UnknownContract__"

        # Check if excluding this contract.
        for exclusion in exclusions:
            if fnmatch(contract_name, exclusion.contract_name) and (
                not exclusion.method_name or exclusion.method_name == "*"
            ):
                # Skip this whole source.
                return

        contract_coverage = source_coverage.include(contract_name)

        def _exclude_fn(_name: str) -> bool:
            for _exclusion in exclusions:
                if fnmatch(contract_coverage.name, _exclusion.contract_name) and fnmatch(
                    _name, _exclusion.method_name
                ):
                    # This function should be skipped.
                    return True

            return False

        def _profile(_name: str, _full_name: str):
            # Ensure function isn't excluded.
            if _exclude_fn(_name):
                return

            _function_coverage = contract_coverage.include(_name, _full_name)

            # Only put the builtin-tags we expect users to be able to cover.
            tag = (
                str(item["dev"])
                if item.get("dev")
                and isinstance(item["dev"], str)
                and item["dev"].startswith("dev: ")
                and RuntimeErrorType.USER_ASSERT.value not in item["dev"]
                else None
            )
            _function_coverage.profile_statement(pc_int, location=location, tag=tag)

        # Some statements are too difficult to know right away where they belong,
        # such as statement related to kwarg-default auto-generated implicit lookups.
        # function_name -> (pc, location)
        pending_statements: dict[str, list[tuple[int, SourceLocation]]] = {}

        for pc, item in contract_source.pcmap.root.items():
            pc_int = int(pc)
            if pc_int < 0:
                continue

            location: Optional[SourceLocation]
            if item.get("location"):
                location_list = item["location"]
                if not isinstance(location_list, (list, tuple)):
                    raise TypeError(f"Unexpected location type '{type(location_list)}'.")

                # NOTE: Only doing 0 because mypy for some reason thinks it is optional.
                location = (
                    location_list[0] or 0,
                    location_list[1] or 0,
                    location_list[2] or 0,
                    location_list[3] or 0,
                )
            else:
                location = None

            if location is not None and not isinstance(location, tuple):
                # Only really for mypy.
                raise TypeError(f"Received unexpected type for location '{location}'.")

            if not location and not item.get("dev"):
                # Not a statement we can measure.
                continue

            if location:
                function = contract_source.lookup_function(location)
                if not function:
                    # Not sure if this happens.
                    continue

                matching_abis = [
                    a for a in contract_source.contract_type.methods if a.name == function.name
                ]
                if len(matching_abis) > 1:
                    # In Vyper, if there are multiple method ABIs with the same name,
                    # that is evidence of the default key-word argument generated methods.

                    is_part_of_signature = location[0] < function.offset
                    if is_part_of_signature and location[0] != location[2]:
                        # This likely is not a real statement, but not really sure what this is.
                        continue

                    # In Vyper, the ABI with the most inputs should be the one without extra steps.
                    longest_abi = max(matching_abis, key=lambda x: len(x.inputs))
                    if is_part_of_signature and longest_abi.name in pending_statements:
                        pending_statements[longest_abi.name].append((pc_int, location))
                    elif is_part_of_signature:
                        pending_statements[longest_abi.name] = [(pc_int, location)]
                    else:
                        # Put actual source statements under the ABI with all parameters as inputs.
                        _profile(longest_abi.name, longest_abi.selector)

                elif len(matching_abis) == 1:
                    _profile(function.name, matching_abis[0].selector)

                elif len(matching_abis) == 0:
                    # Is likely an internal method.
                    _profile(function.name, function.full_name or function.name)

            else:
                _profile("__builtin__", "__builtin__")

        if pending_statements:
            # Handle auto-generated kwarg-default statements here.
            # Sort each statement into buckets mapping to the method it belongs in.
            for fn_name, pending_ls in pending_statements.items():
                matching_abis = [
                    m for m in contract_source.contract_type.methods if m.name == fn_name
                ]
                longest_abi = max(matching_abis, key=lambda x: len(x.inputs))
                autogenerated_abis = [
                    abi for abi in matching_abis if abi.selector != longest_abi.selector
                ]
                # Sort the autogenerated ABIs so we can loop through them in the correct order.
                autogenerated_abis.sort(key=lambda a: len(a.inputs))
                buckets: dict[str, list[tuple[int, SourceLocation]]] = {
                    a.selector: [] for a in autogenerated_abis
                }
                selector_index = 0
                selector = autogenerated_abis[0].selector
                # Must loop through PCs from smallest to greatest for this to work.
                pending_ls.sort()
                jump_threshold = 10
                for _pc, loc in pending_ls:
                    if selector_index < len(autogenerated_abis):
                        selector = autogenerated_abis[selector_index].selector

                    if not buckets[selector]:
                        # No need for bounds checking when the bucket is empty.
                        buckets[selector].append((_pc, loc))
                        continue

                    last_pc = buckets[selector][-1][0]

                    # Check if jumped.
                    distance = _pc - last_pc
                    if distance > jump_threshold:
                        selector_index += 1
                        if selector_index >= len(autogenerated_abis):
                            break

                        selector = autogenerated_abis[selector_index].selector
                        buckets[selector].append((_pc, loc))
                    else:
                        buckets[selector].append((_pc, loc))

                for full_name, statements in buckets.items():
                    for _pc, location in statements:
                        if _exclude_fn(fn_name):
                            continue

                        function_coverage = contract_coverage.include(fn_name, full_name)
                        function_coverage.profile_statement(_pc, location=location)

        # After handling all methods with locations, let's also add the auto-getters,
        # which are not present in the source map.
        for method in contract_source.contract_type.view_methods:
            if method.selector not in [fn.full_name for fn in contract_coverage.functions]:
                if _exclude_fn(method.name):
                    return

                # Auto-getter found. Profile function without statements.
                contract_coverage.include(method.name, method.selector)

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

    def enrich_error(self, err: ContractLogicError) -> ContractLogicError:
        try:
            dev_message = err.dev_message
        except ValueError:
            # Not available.
            return err

        if not dev_message:
            return err

        err_str = dev_message.replace("dev: ", "")
        error_type = None
        if err_str in [m.value for m in RuntimeErrorType]:
            # Is a builtin compiler error.
            error_type = RuntimeErrorType(err_str)

        elif "bounds check" in err_str:
            error_type = RuntimeErrorType.INTEGER_BOUNDS_CHECK

        else:
            # Check names
            for name, _type in [(m.name, m) for m in RuntimeErrorType]:
                if err_str == name:
                    error_type = _type
                    break

        if not error_type:
            # Not a builtin compiler error; cannot enrich.
            return err

        runtime_error_cls = RUNTIME_ERROR_MAP[error_type]
        tx_kwargs: dict = {
            "contract_address": err.contract_address,
            "source_traceback": err.source_traceback,
            "trace": err.trace,
            "txn": err.txn,
        }
        return (
            runtime_error_cls(err_str.split(" ")[0], **tx_kwargs)
            if runtime_error_cls == IntegerBoundsCheck
            else runtime_error_cls(**tx_kwargs)
        )

    def trace_source(
        self, contract_source: ContractSource, trace: TraceAPI, calldata: HexBytes
    ) -> SourceTraceback:
        frames = trace.get_raw_frames()
        return self._get_traceback(contract_source, frames, calldata)

    def _get_traceback(
        self,
        contract_src: ContractSource,
        frames: Iterator[dict],
        calldata: HexBytes,
        previous_depth: Optional[int] = None,
    ) -> SourceTraceback:
        traceback = SourceTraceback.model_validate([])
        method_id = HexBytes(calldata[:4])
        completed = False
        pcmap = PCMap.model_validate({})

        for frame in frames:
            if frame["op"] in [c.value for c in CALL_OPCODES]:
                start_depth = frame["depth"]
                called_contract, sub_calldata = self._create_contract_from_call(frame)
                if called_contract:
                    ext = get_full_extension(Path(called_contract.source_id))
                    if ext in [x for x in FileType]:
                        # Called another Vyper contract.
                        sub_trace = self._get_traceback(
                            called_contract, frames, sub_calldata, previous_depth=frame["depth"]
                        )
                        traceback.extend(sub_trace)

                    else:
                        # Not a Vyper contract!
                        compiler = self.compiler_manager.registered_compilers[ext]
                        try:
                            sub_trace = compiler.trace_source(
                                called_contract.contract_type, frames, sub_calldata
                            )
                            traceback.extend(sub_trace)
                        except NotImplementedError:
                            # Compiler not supported. Fast forward out of this call.
                            for fr in frames:
                                if fr["depth"] <= start_depth:
                                    break

                            continue

                else:
                    # Contract not found. Fast forward out of this call.
                    for fr in frames:
                        if fr["depth"] <= start_depth:
                            break

                    continue

            elif frame["op"] in _RETURN_OPCODES:
                # For the base CALL, don't mark as completed until trace is gone.
                # This helps in cases where we failed to detect a subcall properly.
                completed = previous_depth is not None

            pcs_to_try_adding = set()
            if "PUSH" in frame["op"] and frame["pc"] in contract_src.pcmap:
                # Check if next op is SSTORE to properly use AST from push op.
                next_frame: Optional[dict] = frame
                loc = contract_src.pcmap[frame["pc"]]
                pcs_to_try_adding.add(frame["pc"])

                while next_frame and "PUSH" in next_frame["op"]:
                    next_frame = next(frames, None)
                    if next_frame and "PUSH" in next_frame["op"]:
                        pcs_to_try_adding.add(next_frame["pc"])

                is_non_payable_hit = False
                if next_frame and next_frame["op"] == "SSTORE":
                    push_location = tuple(loc["location"])  # type: ignore
                    pcmap = PCMap.model_validate({next_frame["pc"]: {"location": push_location}})

                elif next_frame and next_frame["op"] in _RETURN_OPCODES:
                    completed = True

                else:
                    pcmap = contract_src.pcmap
                    dev_val = str((loc.get("dev") or "")).replace("dev: ", "")
                    is_non_payable_hit = dev_val == RuntimeErrorType.NONPAYABLE_CHECK.value

                if not is_non_payable_hit and next_frame:
                    frame = next_frame

            else:
                pcmap = contract_src.pcmap

            pcs_to_try_adding.add(frame["pc"])
            pcs_to_try_adding = {pc for pc in pcs_to_try_adding if pc in pcmap}
            if not pcs_to_try_adding:
                if (
                    frame["op"] == "REVERT"
                    and frame["pc"] + 1 in pcmap
                    and RuntimeErrorType.USER_ASSERT.value
                    in str(pcmap[frame["pc"] + 1].get("dev", ""))
                ):
                    # Not sure why this happens. Maybe an off-by-1 bug in Vyper.
                    pcs_to_try_adding.add(frame["pc"] + 1)

            pc_groups: list[list] = []
            for pc in pcs_to_try_adding:
                location = (
                    cast(tuple[int, int, int, int], tuple(pcmap[pc].get("location") or [])) or None
                )
                dev_item = pcmap[pc].get("dev", "")
                dev = str(dev_item).replace("dev: ", "")

                done = False
                for group in pc_groups:
                    if group[0] != location:
                        continue

                    group[1].add(pc)
                    dev = group[2] = dev or group[2]
                    done = True
                    break

                if not done:
                    # New group.
                    pc_groups.append([location, {pc}, dev])

            dev_messages = contract_src.contract_type.dev_messages or {}
            for location, pcs, dev in pc_groups:
                if dev in [m.value for m in RuntimeErrorType if m != RuntimeErrorType.USER_ASSERT]:
                    error_type = RuntimeErrorType(dev)
                    if (
                        error_type != RuntimeErrorType.NONPAYABLE_CHECK
                        and traceback.last is not None
                    ):
                        # If the error type is not the non-payable check,
                        # it happened in the last method.
                        name = traceback.last.closure.name
                        full_name = traceback.last.closure.full_name

                    elif method_id in contract_src.contract_type.methods:
                        # For non-payable checks, they should hit here.
                        method_checked = contract_src.contract_type.methods[method_id]
                        name = method_checked.name
                        full_name = method_checked.selector

                    else:
                        # Not sure if possible to get here.
                        name = error_type.name.lower()
                        full_name = name

                    if (
                        dev == RuntimeErrorType.INVALID_CALLDATA_OR_VALUE.value
                        and len(traceback.source_statements) > 0
                    ):
                        # NOTE: Skip adding invalid calldata / value checks when
                        # we have already hit source statements. The reason for this
                        # is because of misleading Vyper optimizations sharing revert PCs.
                        continue

                    # Empty source (is builtin)
                    traceback.add_builtin_jump(
                        name,
                        f"dev: {dev}",
                        full_name=full_name,
                        pcs=pcs,
                        source_path=contract_src.source_path,
                    )
                    continue

                elif not location:
                    # Unknown.
                    continue

                if not (function := contract_src.lookup_function(location, method_id=method_id)):
                    continue

                if (
                    not traceback.last
                    or traceback.last.closure.full_name != function.full_name
                    or not isinstance(traceback.last.closure, Function)
                ):
                    depth = (
                        frame["depth"] + 1
                        if traceback.last and traceback.last.depth == frame["depth"]
                        else frame["depth"]
                    )

                    traceback.add_jump(
                        location,
                        function,
                        depth,
                        pcs=pcs,
                        source_path=contract_src.source_path,
                    )
                else:
                    traceback.extend_last(location, pcs=pcs)

                if len(traceback.source_statements) > 0:
                    last_statement = traceback.source_statements[-1]
                    if dev.endswith(RuntimeErrorType.USER_ASSERT.value) or any(
                        DEV_MSG_PATTERN.match(str(s)) for s in str(last_statement).splitlines()
                    ):
                        # Add dev message to user assert
                        for lineno in range(
                            last_statement.end_lineno, last_statement.begin_lineno - 1, -1
                        ):
                            if lineno in dev_messages:
                                last_statement.type = dev_messages[lineno]

            if completed:
                break

        return traceback

    def _create_contract_from_call(self, frame: dict) -> tuple[Optional[ContractSource], HexBytes]:
        evm_frame = TraceFrame(**frame)
        data = create_call_node_data(evm_frame)
        calldata = data.get("calldata", HexBytes(""))
        if not (address := (data.get("address", evm_frame.contract_address) or None)):
            return None, calldata

        try:
            address = self.provider.network.ecosystem.decode_address(address)
        except Exception:
            return None, calldata

        if address not in self.chain_manager.contracts:
            return None, calldata

        called_contract = self.chain_manager.contracts[address]
        return self.local_project._create_contract_source(called_contract), calldata


def _safe_append(data: dict, version: Union[Version, SpecifierSet], paths: Union[Path, set]):
    if isinstance(paths, Path):
        paths = {paths}
    if version in data:
        data[version] = data[version].union(paths)
    else:
        data[version] = paths


def _is_revert_jump(op: str, value: Optional[int], revert_pc: int) -> bool:
    return op == "JUMPI" and value is not None and value == revert_pc


def _has_empty_revert(opcodes: list[str]) -> bool:
    return (len(opcodes) > 12 and opcodes[-13] == "JUMPDEST" and opcodes[-9] == "REVERT") or (
        len(opcodes) > 4 and opcodes[-5] == "JUMPDEST" and opcodes[-1] == "REVERT"
    )


def _get_pcmap(bytecode: dict) -> PCMap:
    # Find the non payable value check.
    src_info = bytecode["sourceMapFull"] if "sourceMapFull" in bytecode else bytecode["sourceMap"]
    pc_data = {pc: {"location": ln} for pc, ln in src_info["pc_pos_map"].items()}
    if not pc_data:
        return PCMap.model_validate({})

    # Apply other errors.
    errors = src_info["error_map"]
    for err_pc, error_type in errors.items():
        use_loc = True
        if "safemul" in error_type or "safeadd" in error_type or "bounds check" in error_type:
            # NOTE: Bound check may also occur for underflow.
            error_str = RuntimeErrorType.INTEGER_OVERFLOW.value
        elif "safesub" in error_type or "clamp" in error_type:
            error_str = RuntimeErrorType.INTEGER_UNDERFLOW.value
        elif "safediv" in error_type or "clamp gt 0" in error_type:
            error_str = RuntimeErrorType.DIVISION_BY_ZERO.value
        elif "safemod" in error_type:
            error_str = RuntimeErrorType.MODULO_BY_ZERO.value
        elif "bounds check" in error_type:
            error_str = RuntimeErrorType.INDEX_OUT_OF_RANGE.value
        elif "user assert" in error_type.lower() or "user revert" in error_type.lower():
            # Mark user-asserts so the Ape can correctly find dev messages.
            error_str = RuntimeErrorType.USER_ASSERT.value
        elif "fallback function" in error_type:
            error_str = RuntimeErrorType.FALLBACK_NOT_DEFINED.value
            use_loc = False
        elif "bad calldatasize or callvalue" in error_type:
            # Only on >=0.3.10.
            # NOTE: We are no longer able to get Nonpayable checks errors since they
            # are now combined.
            error_str = RuntimeErrorType.INVALID_CALLDATA_OR_VALUE.value
        elif "nonpayable check" in error_type:
            error_str = RuntimeErrorType.NONPAYABLE_CHECK.value
        else:
            error_str = ""
            error_type_name = error_type.upper().replace(" ", "_")
            for _type in RuntimeErrorType:
                if _type.name == error_type_name:
                    error_str = _type.value
                    break

            error_str = error_str or error_type_name
            use_loc = False

        location = None
        if use_loc:
            # Add surrounding locations
            for pc in range(int(err_pc), -1, -1):
                if (
                    (data := pc_data.get(f"{pc}"))
                    and "dev" not in data
                    and (location := data.get("location"))
                ):
                    break

        if err_pc in pc_data:
            pc_data[err_pc]["dev"] = f"dev: {error_str}"
        else:
            pc_data[err_pc] = {"dev": f"dev: {error_str}", "location": location}

    return PCMap.model_validate(pc_data)


def _get_legacy_pcmap(ast: ASTNode, src_map: list[SourceMapItem], opcodes: list[str]):
    """
    For Vyper versions <= 0.3.7, allows us to still get a PCMap.
    """

    pc = 0
    pc_map_list: list[tuple[int, dict[str, Optional[Any]]]] = []
    last_value = None
    revert_pc = -1
    if _has_empty_revert(opcodes):
        revert_pc = _get_revert_pc(opcodes)

    processed_opcodes = []

    # There is only 1 non-payable check and it happens early in the bytecode.
    non_payable_check_found = False

    # There is at most 1 fallback error PC
    fallback_found = False

    while src_map and opcodes:
        src = src_map.pop(0)
        op = opcodes.pop(0)
        processed_opcodes.append(op)
        pc += 1

        # If immutable member load, ignore increasing pc by push size.
        if _is_immutable_member_load(opcodes):
            last_value = int(opcodes.pop(0), 16)
            # Add the push number, e.g. PUSH1 adds `1`.
            num_pushed = int(op[4:])
            pc += num_pushed

        # Add content PC item.
        # Also check for compiler runtime error handling.
        # Runtime error locations are marked in the PCMap for further analysis.
        if src.start is not None and src.length is not None:
            stmt = ast.get_node(src)
            if stmt:
                # Add located item.
                line_nos = list(stmt.line_numbers)
                item: dict = {"location": line_nos}
                is_revert_jump = _is_revert_jump(op, last_value, revert_pc)
                if op == "REVERT" or is_revert_jump:
                    dev = None
                    if stmt.ast_type in ("AugAssign", "BinOp"):
                        # SafeMath
                        for node in stmt.children:
                            dev = RuntimeErrorType.from_operator(node.ast_type)
                            if dev:
                                break

                    elif stmt.ast_type == "Subscript":
                        dev = RuntimeErrorType.INDEX_OUT_OF_RANGE

                    else:
                        # This is needed for finding the corresponding dev message.
                        dev = RuntimeErrorType.USER_ASSERT

                    if dev:
                        val = f"dev: {dev.value}"
                        if is_revert_jump and len(pc_map_list) >= 1:
                            pc_map_list[-1][1]["dev"] = val
                        else:
                            item["dev"] = val

                pc_map_list.append((pc, item))

        elif not fallback_found and _is_fallback_check(opcodes, op):
            # You can tell this is the Fallback jump because it is checking for the method ID.
            item = {"dev": f"dev: {RuntimeErrorType.FALLBACK_NOT_DEFINED.value}", "location": None}
            # PC is actually the one before but it easier to detect here.
            pc_map_list.append((pc - 1, item))
            fallback_found = True

        elif not non_payable_check_found and _is_non_payable_check(opcodes, op, revert_pc):
            item = {"dev": _NON_PAYABLE_STR, "location": None}
            pc_map_list.append((pc, item))
            non_payable_check_found = True

        elif op == "REVERT":
            # Source-less revert found, use latest item with a source.
            for item in [x[1] for x in pc_map_list[::-1] if x[1]["location"]]:
                if not item.get("dev"):
                    item["dev"] = f"dev: {RuntimeErrorType.USER_ASSERT.value}"
                    break

    pcmap_data = dict(pc_map_list)
    return PCMap.model_validate(pcmap_data)


def _find_non_payable_check(src_map: list[SourceMapItem], opcodes: list[str]) -> Optional[int]:
    pc = 0
    revert_pc = -1
    if _has_empty_revert(opcodes):
        revert_pc = _get_revert_pc(opcodes)

    while src_map and opcodes:
        op = opcodes.pop(0)
        pc += 1

        # If immutable member load, ignore increasing pc by push size.
        if _is_immutable_member_load(opcodes):
            # Add the push number, e.g. PUSH1 adds `1`.
            pc += int(op[4:])

        if _is_non_payable_check(opcodes, op, revert_pc):
            return pc

    return None


def _is_non_payable_check(opcodes: list[str], op: str, revert_pc: int) -> bool:
    return (
        len(opcodes) >= 3
        and op == "CALLVALUE"
        and "PUSH" in opcodes[0]
        and is_0x_prefixed(opcodes[1])
        and _is_revert_jump(opcodes[2], int(opcodes[1], 16), revert_pc)
    )


def _get_revert_pc(opcodes: list[str]) -> int:
    """
    Starting in vyper 0.2.14, reverts without a reason string are optimized
    with a jump to the "end" of the bytecode.
    """
    return (
        len(opcodes)
        + sum(int(i[4:]) - 1 for i in opcodes if i.startswith("PUSH"))
        - _EMPTY_REVERT_OFFSET
    )


def _is_immutable_member_load(opcodes: list[str]):
    is_code_copy = len(opcodes) > 5 and opcodes[5] == "CODECOPY"
    return not is_code_copy and opcodes and is_0x_prefixed(opcodes[0])


def _extend_return(function: Function, traceback: SourceTraceback, last_pc: int, source_path: Path):
    return_ast_result = [x for x in function.ast.children if x.ast_type == "Return"]
    if not return_ast_result:
        return

    # Ensure return statement added.
    # Sometimes it is missing from the PCMap otherwise.
    return_ast = return_ast_result[-1]
    location = return_ast.line_numbers

    last_lineno = max(0, location[2] - 1)
    for frameset in traceback.root[::-1]:
        if frameset.end_lineno is not None:
            last_lineno = frameset.end_lineno
            break

    start = last_lineno + 1
    last_pcs = {last_pc + 1} if last_pc else set()
    if traceback.last:
        traceback.last.extend(location, pcs=last_pcs, ws_start=start)
    else:
        # Not sure if it ever gets here, but type-checks say it could.
        traceback.add_jump(location, function, 1, last_pcs, source_path=source_path)


def _is_fallback_check(opcodes: list[str], op: str) -> bool:
    return (
        "JUMP" in op
        and len(opcodes) >= 7
        and opcodes[0] == "JUMPDEST"
        and opcodes[6] == "SHR"
        and opcodes[5] == "0xE0"
    )
