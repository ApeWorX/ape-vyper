import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple, Union, cast

import vvm  # type: ignore
from ape.api import PluginConfig
from ape.api.compiler import CompilerAPI
from ape.exceptions import ContractLogicError
from ape.types import ContractType, SourceTraceback, TraceFrame
from ape.utils import cached_property, get_relative_path
from eth_utils import is_0x_prefixed
from ethpm_types import ASTNode, HexBytes, PackageManifest, PCMap
from ethpm_types.ast import ASTClassification
from ethpm_types.contract_type import SourceMap
from ethpm_types.source import ContractSource, Function
from evm_trace.enums import CALL_OPCODES
from semantic_version import NpmSpec, Version  # type: ignore
from vvm.exceptions import VyperError  # type: ignore

from ape_vyper.exceptions import (
    RUNTIME_ERROR_MAP,
    RuntimeErrorType,
    VyperCompileError,
    VyperInstallError,
)

DEV_MSG_PATTERN = re.compile(r"#\s*(dev:.+)")
_RETURN_OPCODES = ("RETURN", "REVERT", "STOP")
_FUNCTION_DEF = "FunctionDef"
_FUNCTION_AST_TYPES = (_FUNCTION_DEF, "Name", "arguments")
_EMPTY_REVERT_OFFSET = 18
_NON_PAYABLE_STR = f"dev: {RuntimeErrorType.NONPAYABLE_CHECK.value}"


class VyperConfig(PluginConfig):
    evm_version: Optional[str] = None

    import_remapping: List[str] = []
    """
    Configuration of an import name mapped to a dependency listing.
    To use a specific version of a dependency, specify using ``@`` symbol.

    Usage example::

        vyper:
          import_remapping:
            - "dep_a=dependency_a@0.1.1"
            - "dep_b=dependency"  # Uses only version. Will raise if more than 1.

    """


def _install_vyper(version: Version):
    try:
        vvm.install_vyper(version, show_progress=True)
    except Exception as err:
        raise VyperInstallError(
            f"Unable to install Vyper version: '{version}'.\nReason: {err}"
        ) from err


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
    @property
    def config(self) -> VyperConfig:
        return cast(VyperConfig, self.config_manager.get_config("vyper"))

    @property
    def name(self) -> str:
        return "vyper"

    @property
    def evm_version(self) -> Optional[str]:
        return self.config.evm_version

    def get_imports(
        self, contract_filepaths: List[Path], base_path: Optional[Path] = None
    ) -> Dict[str, List[str]]:
        base_path = (base_path or self.project_manager.contracts_folder).absolute()
        import_map = {}
        for path in contract_filepaths:
            content = path.read_text().splitlines()
            source_id = str(get_relative_path(path.absolute(), base_path.absolute()))
            for line in content:
                if line.startswith("import "):
                    import_line_parts = line.replace("import ", "").split(" ")
                    suffix = import_line_parts[0].strip().replace(".", os.path.sep)

                elif line.startswith("from ") and " import " in line:
                    import_line_parts = line.replace("from ", "").split(" ")
                    module_name = import_line_parts[0].strip().replace(".", os.path.sep)
                    suffix = os.path.sep.join([module_name, import_line_parts[2].strip()])

                else:
                    # Not an import line
                    continue

                # NOTE: Defaults to JSON (assuming from input JSON or a local JSON),
                #  unless a Vyper file exists.
                ext = "vy" if (base_path / f"{suffix}.vy").is_file() else "json"

                import_source_id = f"{suffix}.{ext}"
                if source_id not in import_map:
                    import_map[source_id] = [import_source_id]
                elif import_source_id not in import_map[source_id]:
                    import_map[source_id].append(import_source_id)

        return import_map

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

        except ImportError:
            return None

        # Strip off parts from source-installation
        version = Version.coerce(vyper.__version__)
        return Version(major=version.major, minor=version.minor, patch=version.patch)

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
        try:
            from vyper.cli import vyper_json  # type: ignore

            return vyper_json
        except ImportError:
            return None

    @property
    def import_remapping(self) -> Dict[str, Dict]:
        """
        Configured interface imports from dependencies.
        """

        interfaces = {}
        dependencies: Dict[str, PackageManifest] = {}

        for remapping in self.config.import_remapping:
            key, value = remapping.split("=")

            if remapping in dependencies:
                dependency = dependencies[remapping]
            else:
                parts = value.split("@")
                dep_name = parts[0]
                dependency_versions = self.project_manager.dependencies[dep_name]
                if not dependency_versions:
                    raise VyperCompileError(f"Missing dependency '{dep_name}'.")

                elif len(parts) == 1 and len(dependency_versions) < 2:
                    # Use only version.
                    version = list(dependency_versions.keys())[0]

                elif parts[1] not in dependency_versions:
                    raise VyperCompileError(f"Missing dependency '{dep_name}'.")

                else:
                    version = parts[1]

                dependency = dependency_versions[version].compile()
                dependencies[remapping] = dependency

            for name, ct in (dependency.contract_types or {}).items():
                interfaces[f"{key}/{name}.json"] = {"abi": [x.dict() for x in ct.abi]}

        return interfaces

    def compile(
        self, contract_filepaths: List[Path], base_path: Optional[Path] = None
    ) -> List[ContractType]:
        contract_types = []
        base_path = base_path or self.config_manager.contracts_folder
        sources = [p for p in contract_filepaths if p.parent.name != "interfaces"]
        version_map = self.get_version_map(sources)
        compiler_data = self._get_compiler_arguments(version_map, base_path)
        all_settings = self.get_compiler_settings(sources, base_path=base_path)

        for vyper_version, source_paths in version_map.items():
            settings = all_settings.get(vyper_version, {})
            path_args = {str(get_relative_path(p.absolute(), base_path)): p for p in source_paths}
            input_json = {
                "language": "Vyper",
                "settings": settings,
                "sources": {s: {"content": p.read_text()} for s, p in path_args.items()},
            }
            interfaces = self.import_remapping
            if interfaces:
                input_json["interfaces"] = interfaces

            vyper_binary = compiler_data[vyper_version]["vyper_binary"]
            try:
                result = vvm.compile_standard(
                    input_json,
                    base_path=base_path,
                    vyper_version=vyper_version,
                    vyper_binary=vyper_binary,
                )
            except VyperError as err:
                raise VyperCompileError(err) from err

            def classify_ast(_node: ASTNode):
                if _node.ast_type in _FUNCTION_AST_TYPES:
                    _node.classification = ASTClassification.FUNCTION

                for child in _node.children:
                    classify_ast(child)

            for source_id, output_items in result["contracts"].items():
                content = {
                    i + 1: ln
                    for i, ln in enumerate((base_path / source_id).read_text().splitlines())
                }
                for name, output in output_items.items():
                    # De-compress source map to get PC POS map.
                    ast = ASTNode.parse_obj(result["sources"][source_id]["ast"])
                    classify_ast(ast)

                    # Track function offsets.
                    function_offsets = []
                    for node in ast.children:
                        lineno = node.lineno

                        # NOTE: Constructor is handled elsewhere.
                        if node.ast_type == "FunctionDef" and "__init__" not in content.get(
                            lineno, ""
                        ):
                            function_offsets.append((node.lineno, node.end_lineno))

                    bytecode = output["evm"]["deployedBytecode"]
                    opcodes = bytecode["opcodes"].split(" ")
                    compressed_src_map = SourceMap(__root__=bytecode["sourceMap"])
                    src_map = list(compressed_src_map.parse())[1:]
                    pc = 0
                    pc_map_list: List[Tuple[int, Dict[str, Optional[Any]]]] = []
                    last_value = None
                    revert_pc = -1
                    if _has_empty_revert(opcodes):
                        # Starting in vyper 0.2.14, reverts without a reason string are optimized
                        # with a jump to the "end" of the bytecode.
                        revert_pc = (
                            len(opcodes)
                            + sum(int(i[4:]) - 1 for i in opcodes if i.startswith("PUSH"))
                            - _EMPTY_REVERT_OFFSET
                        )

                    processed_opcodes = []

                    # There is only 1 non-payable check and it happens early in the bytecode.
                    non_payable_check_found = False

                    while src_map and opcodes:
                        src = src_map.pop(0)
                        op = opcodes.pop(0)
                        processed_opcodes.append(op)
                        pc += 1

                        # Detect immutable state member load.
                        # If this is the case, ignore increasing pc by push size.
                        is_code_copy = len(opcodes) > 5 and opcodes[5] == "CODECOPY"

                        if not is_code_copy and opcodes and is_0x_prefixed(opcodes[0]):
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
                                item: Dict = {"location": line_nos}
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

                                    if dev:
                                        val = f"dev: {dev.value}"
                                        if is_revert_jump and len(pc_map_list) >= 1:
                                            pc_map_list[-1][1]["dev"] = val
                                        else:
                                            item["dev"] = val

                                pc_map_list.append((pc, item))

                        elif (
                            not non_payable_check_found
                            and len(opcodes) >= 3
                            and op == "CALLVALUE"
                            and "PUSH" in opcodes[0]
                            and is_0x_prefixed(opcodes[1])
                            and _is_revert_jump(opcodes[2], int(opcodes[1], 16), revert_pc)
                        ):
                            item = {"dev": _NON_PAYABLE_STR, "location": None}
                            pc_map_list.append((pc, item))
                            non_payable_check_found = True

                    # Find content-specified dev messages.
                    dev_messages = {}
                    for line_no, line in content.items():
                        if match := re.search(DEV_MSG_PATTERN, line):
                            dev_messages[line_no] = match.group(1).strip()

                    contract_type = ContractType(
                        ast=ast,
                        contractName=name,
                        sourceId=source_id,
                        deploymentBytecode={"bytecode": output["evm"]["bytecode"]["object"]},
                        runtimeBytecode={"bytecode": bytecode["object"]},
                        abi=output["abi"],
                        sourcemap=compressed_src_map,
                        pcmap=PCMap.parse_obj(dict(pc_map_list)),
                        userdoc=output["userdoc"],
                        devdoc=output["devdoc"],
                        dev_messages=dev_messages,
                    )
                    contract_types.append(contract_type)

        return contract_types

    def get_version_map(
        self, contract_filepaths: List[Path], base_path: Optional[Path] = None
    ) -> Dict[Version, Set[Path]]:
        version_map: Dict[Version, Set[Path]] = {}
        source_path_by_pragma_spec: Dict[NpmSpec, Set[Path]] = {}
        source_paths_without_pragma = set()

        # Sort contract_filepaths to promote consistent, reproduce-able behavior
        for path in sorted(contract_filepaths):
            pragma_spec = get_pragma_spec(path.read_text())
            if not pragma_spec:
                source_paths_without_pragma.add(path)
            else:
                _safe_append(source_path_by_pragma_spec, pragma_spec, path)

        # Install all requires versions *before* building map
        for pragma_spec, path_set in source_path_by_pragma_spec.items():
            can_install = pragma_spec.select(self.installed_versions)
            if can_install:
                continue

            available_vyper_version = pragma_spec.select(self.available_versions)
            if available_vyper_version and available_vyper_version != self.package_version:
                _install_vyper(available_vyper_version)

            elif available_vyper_version:
                raise VyperInstallError(
                    f"Unable to install vyper version '{available_vyper_version}'."
                )
            else:
                raise VyperInstallError("No available version to install.")

        # By this point, all the of necessary versions will be installed.
        # Thus, we will select only the best versions to use per source set.
        for pragma_spec, path_set in source_path_by_pragma_spec.items():
            version = pragma_spec.select(self.installed_versions)
            _safe_append(version_map, version, path_set)

        if not self.installed_versions:
            # If we have no installed versions by this point, we need to install one.
            # This happens when there are no pragmas in any sources and no vyper installations.
            _install_vyper(max(self.available_versions))

        # Handle no-pragma sources
        if source_paths_without_pragma:
            max_installed_vyper_version = (
                max(version_map) if version_map else max(self.installed_versions)
            )
            _safe_append(version_map, max_installed_vyper_version, source_paths_without_pragma)

        return version_map

    def get_compiler_settings(
        self, contract_filepaths: List[Path], base_path: Optional[Path] = None
    ) -> Dict[Version, Dict]:
        contracts_path = base_path or self.config_manager.contracts_folder
        files_by_vyper_version = self.get_version_map(contract_filepaths, base_path=contracts_path)
        if not files_by_vyper_version:
            return {}

        compiler_data = self._get_compiler_arguments(files_by_vyper_version, contracts_path)
        settings = {}
        for version, data in compiler_data.items():
            source_paths = files_by_vyper_version.get(version)
            if not source_paths:
                continue

            version_settings: Dict = {"optimize": True}
            path_args = {
                str(get_relative_path(p.absolute(), contracts_path)): p for p in source_paths
            }
            version_settings["outputSelection"] = {s: ["*"] for s in path_args}
            if data["evm_version"]:
                version_settings["evmVersion"] = data["evm_version"]

            settings[version] = version_settings

        return settings

    def _get_compiler_arguments(self, version_map: Dict, base_path: Path) -> Dict[Version, Dict]:
        base_path = base_path or self.project_manager.contracts_folder
        arguments_map = {}
        for vyper_version, source_paths in version_map.items():
            bin_arg = self._get_vyper_bin(vyper_version)
            arguments_map[vyper_version] = {
                "base_path": str(base_path),
                "evm_version": self.evm_version,
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

        if err_str in [m.value for m in RuntimeErrorType]:
            # Is a builtin compiler error.
            runtime_error_type = RuntimeErrorType(err_str)
            runtime_error_cls = RUNTIME_ERROR_MAP[runtime_error_type]
            return runtime_error_cls(
                contract_address=err.contract_address,
                source_traceback=err.source_traceback,
                trace=err.trace,
                txn=err.txn,
            )

        else:
            # Not a builtin compiler error; cannot enrich.
            return err

    def trace_source(
        self, contract_type: ContractType, trace: Iterator[TraceFrame], calldata: HexBytes
    ) -> SourceTraceback:
        source_contract_type = self.project_manager._create_contract_source(contract_type)
        if not source_contract_type:
            return SourceTraceback.parse_obj([])

        return self._get_traceback(source_contract_type, trace, calldata)

    def _get_traceback(
        self, contract_src: ContractSource, trace: Iterator[TraceFrame], calldata: HexBytes
    ) -> SourceTraceback:
        traceback = SourceTraceback.parse_obj([])
        function = None
        last_pc = None

        for frame in trace:
            if frame.op in CALL_OPCODES:
                called_contract, sub_calldata = self._create_contract_from_call(frame)
                if called_contract:
                    ext = Path(called_contract.source_id).suffix
                    if not ext.endswith(".vy"):
                        # Not a Vyper contract!
                        compiler = self.compiler_manager.registered_compilers[ext]
                        try:
                            sub_trace = compiler.trace_source(
                                called_contract.contract_type, trace, sub_calldata
                            )
                            traceback.extend(sub_trace)
                        except NotImplementedError:
                            # Compiler not supported. Fast forward out of this call.
                            for fr in trace:
                                if fr.op == "RETURN":
                                    break

                    else:
                        sub_trace = self._get_traceback(called_contract, trace, sub_calldata)
                        traceback.extend(sub_trace)

                else:
                    # Contract not found. Fast forward out of this call.
                    for fr in trace:
                        if fr.op == "RETURN":
                            break

            elif frame.op in _RETURN_OPCODES:
                if frame.op == "RETURN" and function:
                    return_ast_result = [x for x in function.ast.children if x.ast_type == "Return"]
                    if return_ast_result:
                        # Ensure return statement added.
                        # Sometimes it is missing from the PCMap otherwise.
                        return_ast = return_ast_result[-1]
                        location = return_ast.line_numbers

                        last_lineno = max(0, location[2] - 1)
                        for frameset in traceback.__root__[::-1]:
                            if frameset.end_lineno is not None:
                                last_lineno = frameset.end_lineno
                                break

                        start = last_lineno + 1
                        last_pcs = {last_pc + 1} if last_pc else {}
                        traceback.last.extend(location, pcs=last_pcs, ws_start=start)

                # Completed!
                return traceback

            if "PUSH" in frame.op and frame.pc in contract_src.pcmap:
                # Check if next op is SSTORE to properly use AST from push op.
                next_frame = next(trace, None)
                is_non_payable_hit = False
                if next_frame and next_frame.op == "SSTORE":
                    push_location = tuple(contract_src.pcmap[frame.pc]["location"])  # type: ignore
                    pcmap = PCMap.parse_obj({next_frame.pc: {"location": push_location}})
                else:
                    pcmap = contract_src.pcmap
                    dev_val = str((pcmap[frame.pc].get("dev") or "")).replace("dev: ", "")
                    is_non_payable_hit = dev_val == RuntimeErrorType.NONPAYABLE_CHECK.value

                if not is_non_payable_hit and next_frame:
                    frame = next_frame

            else:
                pcmap = contract_src.pcmap

            if frame.pc not in pcmap:
                continue

            method_id = HexBytes(calldata[:4])
            location = cast(Tuple[int, int, int, int], tuple(pcmap[frame.pc].get("location") or []))
            dev_item = pcmap[frame.pc].get("dev", "")
            dev = str(dev_item).replace("dev: ", "")
            if not location and dev in [m.value for m in RuntimeErrorType]:
                error_type = RuntimeErrorType(dev)
                if error_type != RuntimeErrorType.NONPAYABLE_CHECK and traceback.last is not None:
                    # If the error type is not the non-payable check,
                    # it happened in the last method.
                    name = traceback.last.name

                elif method_id in contract_src.contract_type.methods:
                    # For non-payable checks, they should hit here.
                    method_checked = contract_src.contract_type.methods[method_id]
                    name = method_checked.name

                else:
                    # Not sure if possible to get here.
                    name = error_type.name.lower()

                # Empty source (is builtin)
                traceback.add_builtin_jump(
                    name, dev_item, self.name, pcs={frame.pc}, source_path=contract_src.source_path
                )
                continue

            elif not location:
                # Unknown.
                continue

            function = contract_src.lookup_function(location, method_id=method_id)
            if not function:
                continue

            if (
                not traceback.last
                or traceback.last.closure.name != function.name
                or not isinstance(traceback.last.closure, Function)
            ):
                depth = (
                    frame.depth + 1
                    if traceback.last and traceback.last.depth == frame.depth
                    else frame.depth
                )
                traceback.add_jump(
                    location,
                    function,
                    depth,
                    pcs={frame.pc},
                    source_path=contract_src.source_path,
                )
            else:
                traceback.extend_last(location, pcs={frame.pc})

            last_pc = frame.pc

        # Never actually hits this return.
        # See `Completed!` comment above.
        return traceback


def _safe_append(data: Dict, version: Union[Version, NpmSpec], paths: Union[Path, Set]):
    if isinstance(paths, Path):
        paths = {paths}
    if version in data:
        data[version] = data[version].union(paths)
    else:
        data[version] = paths


def _is_revert_jump(op: str, value: Optional[int], revert_pc: int) -> bool:
    return op == "JUMPI" and value is not None and value == revert_pc


def _has_empty_revert(opcodes: List[str]) -> bool:
    return (len(opcodes) > 12 and opcodes[-13] == "JUMPDEST" and opcodes[-9] == "REVERT") or (
        len(opcodes) > 4 and opcodes[-5] == "JUMPDEST" and opcodes[-1] == "REVERT"
    )
