import os
import re
import shutil
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple, Union, cast

import vvm  # type: ignore
from ape.api import PluginConfig
from ape.api.compiler import CompilerAPI
from ape.exceptions import ContractLogicError
from ape.types import ContractSourceCoverage, ContractType, SourceTraceback, TraceFrame
from ape.utils import cached_property, get_relative_path
from eth_utils import is_0x_prefixed
from ethpm_types import ASTNode, HexBytes, PackageManifest, PCMap, SourceMapItem
from ethpm_types.ast import ASTClassification
from ethpm_types.contract_type import SourceMap
from ethpm_types.source import ContractSource, Function, SourceLocation
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

                    evm = output["evm"]
                    bytecode = evm["deployedBytecode"]
                    opcodes = bytecode["opcodes"].split(" ")
                    compressed_src_map = SourceMap(__root__=bytecode["sourceMap"])
                    src_map = list(compressed_src_map.parse())[1:]

                    if vyper_version < Version("0.3.8"):
                        pcmap = _get_legacy_pcmap(ast, src_map, opcodes)
                    else:
                        pcmap = _get_pcmap(bytecode, src_map, opcodes)

                    # Find content-specified dev messages.
                    dev_messages = {}
                    for line_no, line in content.items():
                        if match := re.search(DEV_MSG_PATTERN, line):
                            dev_messages[line_no] = match.group(1).strip()

                    contract_type = ContractType(
                        ast=ast,
                        contractName=name,
                        sourceId=source_id,
                        deploymentBytecode={"bytecode": evm["bytecode"]["object"]},
                        runtimeBytecode={"bytecode": bytecode["object"]},
                        abi=output["abi"],
                        sourcemap=compressed_src_map,
                        pcmap=pcmap,
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
            if evm_version := data.get("evm_version"):
                version_settings["evmVersion"] = evm_version

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
            tag = str(item["dev"]) if item.get("dev") else None
            _function_coverage.profile_statement(pc_int, location=location, tag=tag)

        # Some statements are too difficult to know right away where they belong,
        # such as statement related to kwarg-default auto-generated implicit lookups.
        # function_name -> (pc, location)
        pending_statements: Dict[str, List[Tuple[int, SourceLocation]]] = {}

        for pc, item in contract_source.pcmap.__root__.items():
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
                buckets: Dict[str, List[Tuple[int, SourceLocation]]] = {
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
        error_type = None
        if err_str in [m.value for m in RuntimeErrorType]:
            # Is a builtin compiler error.
            error_type = RuntimeErrorType(err_str)

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
        return runtime_error_cls(
            contract_address=err.contract_address,
            source_traceback=err.source_traceback,
            trace=err.trace,
            txn=err.txn,
        )

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
        method_id = HexBytes(calldata[:4])

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
                if frame.op in "RETURN" and function:
                    _extend_return(function, traceback, last_pc, contract_src.source_path)

                # Completed!
                return traceback

            pcs_to_try_adding = set()
            if "PUSH" in frame.op and frame.pc in contract_src.pcmap:
                # Check if next op is SSTORE to properly use AST from push op.
                next_frame: Optional[TraceFrame] = frame
                loc = contract_src.pcmap[frame.pc]
                pcs_to_try_adding.add(frame.pc)

                while next_frame and "PUSH" in next_frame.op:
                    next_frame = next(trace, None)
                    if next_frame and "PUSH" in next_frame.op:
                        pcs_to_try_adding.add(next_frame.pc)

                is_non_payable_hit = False
                if next_frame and next_frame.op == "SSTORE":
                    push_location = tuple(loc["location"])  # type: ignore
                    pcmap = PCMap.parse_obj({next_frame.pc: {"location": push_location}})

                elif next_frame and next_frame.op in _RETURN_OPCODES:
                    if next_frame.op in "RETURN" and function:
                        _extend_return(function, traceback, last_pc, contract_src.source_path)

                    # Completed!
                    return traceback

                else:
                    pcmap = contract_src.pcmap
                    dev_val = str((loc.get("dev") or "")).replace("dev: ", "")
                    is_non_payable_hit = dev_val == RuntimeErrorType.NONPAYABLE_CHECK.value

                if not is_non_payable_hit and next_frame:
                    frame = next_frame

            else:
                pcmap = contract_src.pcmap

            pcs_to_try_adding.add(frame.pc)
            pcs_to_try_adding = {pc for pc in pcs_to_try_adding if pc in pcmap}
            if not pcs_to_try_adding:
                continue

            pc_groups: List[List] = []
            for pc in pcs_to_try_adding:
                location = (
                    cast(Tuple[int, int, int, int], tuple(pcmap[pc].get("location") or [])) or None
                )
                dev_item = pcmap[pc].get("dev", "")
                dev = str(dev_item).replace("dev: ", "")
                done = False
                for group in pc_groups:
                    if group[0] != location:
                        continue

                    group[1].add(pc)
                    group[2] = dev
                    done = True
                    break

                if not done:
                    # New group.
                    pc_groups.append([location, {pc}, dev])

            for location, pcs, dev in pc_groups:
                if not location and dev in [m.value for m in RuntimeErrorType]:
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

                    # Empty source (is builtin)
                    traceback.add_builtin_jump(
                        name,
                        f"dev: {dev}",
                        self.name,
                        full_name=full_name,
                        pcs=pcs,
                        source_path=contract_src.source_path,
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
                    or traceback.last.closure.full_name != function.full_name
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
                        pcs=pcs,
                        source_path=contract_src.source_path,
                    )
                else:
                    traceback.extend_last(location, pcs=pcs)

                last_pc = max(pcs)

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


def _get_pcmap(bytecode: Dict, src_map: List[SourceMapItem], opcodes: List[str]) -> PCMap:
    # Find the non payable value check.
    src_info = bytecode["sourceMapFull"]
    pc_data = {pc: {"location": ln} for pc, ln in src_info["pc_pos_map"].items()}
    if not pc_data:
        return PCMap.parse_obj({})

    non_payable_check = _find_non_payable_check(src_map, opcodes)
    if not non_payable_check:
        non_payable_check = min([int(x) for x in pc_data]) - 1

    pc_data[non_payable_check] = {"dev": _NON_PAYABLE_STR, "location": None}

    # Apply other errors.
    errors = src_info["error_map"]
    for err_pc, error_type in errors.items():
        if "safemul" in error_type or "safeadd" in err_pc:
            error_str = RuntimeErrorType.INTEGER_OVERFLOW.value
        elif "safesub" in error_type:
            error_str = RuntimeErrorType.INTEGER_UNDERFLOW.value
        elif "safediv" in error_type:
            error_str = RuntimeErrorType.DIVISION_BY_ZERO.value
        elif "safemod" in error_type:
            error_str = RuntimeErrorType.MODULO_BY_ZERO.value
        elif "bounds check" in error_type:
            error_str = RuntimeErrorType.INDEX_OUT_OF_RANGE.value
        else:
            error_str = error_type.upper().replace(" ", "_")

        if err_pc in pc_data:
            pc_data[err_pc]["dev"] = f"dev: {error_str}"
        else:
            pc_data[err_pc] = {"dev": f"dev: {error_str}", "location": None}

    return PCMap.parse_obj(pc_data)


def _get_legacy_pcmap(ast: ASTNode, src_map: List[SourceMapItem], opcodes: List[str]):
    """
    For Vyper versions < 0.3.8, allows us to still get a PCMap.
    """

    pc = 0
    pc_map_list: List[Tuple[int, Dict[str, Optional[Any]]]] = []
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

    return PCMap.parse_obj(dict(pc_map_list))


def _find_non_payable_check(src_map: List[SourceMapItem], opcodes: List[str]) -> Optional[int]:
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


def _is_non_payable_check(opcodes: List[str], op: str, revert_pc: int) -> bool:
    return (
        len(opcodes) >= 3
        and op == "CALLVALUE"
        and "PUSH" in opcodes[0]
        and is_0x_prefixed(opcodes[1])
        and _is_revert_jump(opcodes[2], int(opcodes[1], 16), revert_pc)
    )


def _get_revert_pc(opcodes: List[str]) -> int:
    """
    Starting in vyper 0.2.14, reverts without a reason string are optimized
    with a jump to the "end" of the bytecode.
    """
    return (
        len(opcodes)
        + sum(int(i[4:]) - 1 for i in opcodes if i.startswith("PUSH"))
        - _EMPTY_REVERT_OFFSET
    )


def _is_immutable_member_load(opcodes: List[str]):
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
    for frameset in traceback.__root__[::-1]:
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


def _is_fallback_check(opcodes: List[str], op: str) -> bool:
    return (
        "JUMP" in op
        and len(opcodes) >= 7
        and opcodes[0] == "JUMPDEST"
        and opcodes[6] == "SHR"
        and opcodes[5] == "0xE0"
    )
