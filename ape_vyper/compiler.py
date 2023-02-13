import os
import re
import shutil
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Set, Tuple, Union, cast

import vvm  # type: ignore
from ape.api import PluginConfig
from ape.api.compiler import CompilerAPI
from ape.exceptions import APINotImplementedError
from ape.logging import logger
from ape.types import AddressType, ContractType, CoverageItem, LineTraceNode, PCMap, TraceFrame
from ape.utils import cached_property, get_relative_path
from ethpm_types import HexBytes
from ethpm_types.abi import ABIType, MethodABI
from evm_trace import CallType
from evm_trace.geth import _extract_memory
from semantic_version import NpmSpec, Version  # type: ignore

from .exceptions import VyperCompileError, VyperInstallError

EXTENSIONS = (".vy",)


class VyperConfig(PluginConfig):
    evm_version: Optional[str] = None


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
                    import_source_id = (
                        f"{import_line_parts[0].strip().replace('.', os.path.sep)}.vy"
                    )

                elif line.startswith("from ") and " import " in line:
                    import_line_parts = line.replace("from ", "").split(" ")
                    module_name = import_line_parts[0].strip().replace(".", os.path.sep)
                    file_name = f"{import_line_parts[2].strip()}.vy"
                    import_source_id = os.path.sep.join([module_name, file_name])

                else:
                    # Not an import line
                    continue

                import_path = base_path / import_source_id
                if not import_path.is_file() and not str(import_source_id).startswith("vyper"):
                    logger.error(f"Missing import source '{import_path}'.")

                if source_id not in import_map:
                    import_map[source_id] = [import_source_id]
                elif import_source_id not in import_map[source_id]:
                    import_map[source_id].append(source_id)

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
        try:
            from vyper.cli import vyper_json  # type: ignore

            return vyper_json
        except ImportError:
            return None

    def compile(
        self, contract_filepaths: List[Path], base_path: Optional[Path] = None
    ) -> List[ContractType]:
        contract_types = []
        base_path = base_path or self.config_manager.contracts_folder
        version_map = self.get_version_map(
            [p for p in contract_filepaths if p.parent.name != "interfaces"]
        )
        arguments_map = self._get_compiler_arguments(version_map, base_path)

        for vyper_version, source_paths in version_map.items():
            arguments = arguments_map[vyper_version]
            for path in source_paths:
                source = path.read_text()

                try:
                    result = vvm.compile_source(source, **arguments)["<stdin>"]
                except Exception as err:
                    raise VyperCompileError(err) from err

                contract_path = str(get_relative_path(path.absolute(), base_path))

                # NOTE: Vyper doesn't have internal contract type declarations, use filename
                result["contractName"] = Path(contract_path).stem
                result["sourceId"] = contract_path
                result["deploymentBytecode"] = {"bytecode": result["bytecode"]}
                result["runtimeBytecode"] = {"bytecode": result["bytecode_runtime"]}
                result["sourcemap"] = result["source_map"]["pc_pos_map_compressed"]
                result["pcmap"] = result["source_map"]["pc_pos_map"]

                dev_messages = {}
                dev_msg_pattern = re.compile(r"#\s*(dev:.+)")
                for line_index, line in enumerate(source.splitlines()):
                    if match := re.search(dev_msg_pattern, line):
                        dev_messages[line_index + 1] = match.group(1).strip()

                result["dev_messages"] = dev_messages

                contract_types.append(ContractType.parse_obj(result))

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
            version_settings = {"optimize": True}
            if data["evm_version"]:
                version_settings["evmVersion"] = data["evm_version"]

            settings[version] = version_settings

        return settings

    def get_line_trace(
        self,
        trace: Iterator[TraceFrame],
        contract_address: AddressType,
        method_abi: MethodABI,
    ) -> List[LineTraceNode]:
        if not trace:
            return []

        root_contract_type = method_abi.contract_type
        if not root_contract_type:
            # Look it up.
            root_contract_type = self.chain_manager.contracts.get(contract_address)
            if not root_contract_type:
                return []

        source_id = root_contract_type.source_id
        if not source_id:
            # Likely not a local contract.
            return []

        src_maps = {source_id: self.compiler_manager.get_pc_map(root_contract_type)}
        source = self.project_manager.lookup_path(Path(source_id))
        if not source:
            # Definitely not a local contract.
            return []

        ext = Path(source_id).suffix
        if ext not in EXTENSIONS:
            return self._get_line_trace_via_different_compiler(
                ext, trace, contract_address, method_abi
            )

        srcs: Dict[str, Path] = {source_id: source}
        non_local_srcs: Set[str] = set()
        lines: List[LineTraceNode] = []
        Stack = List[Tuple[AddressType, Optional[ContractType], Optional[Union[MethodABI, str]]]]
        call_stack: Stack = [(contract_address, root_contract_type, method_abi)]
        last_op = None
        push_src = None

        for frame in trace:
            stack = frame.raw["stack"]

            if frame.op in (
                CallType.CALL.value,
                CallType.DELEGATECALL.value,
                CallType.STATICCALL.value,
            ):
                # Find matching method.
                mem = [HexBytes(m) for m in frame.raw["memory"]]
                stack = [HexBytes(s) for s in stack]
                if frame.op == CallType.CALL.value:
                    data = _extract_memory(offset=stack[-4], size=stack[-5], memory=mem)
                elif frame.op == CallType.DELEGATECALL.value:
                    data = _extract_memory(offset=stack[-3], size=stack[-4], memory=mem)
                else:
                    data = _extract_memory(offset=stack[-3], size=stack[-4], memory=mem)

                raw_address = stack[-2][-20:]
                if not raw_address:
                    continue

                address = self.provider.network.ecosystem.decode_address(raw_address)
                contract_type = self.chain_manager.contracts.get(address)
                if not contract_type:
                    call_stack.append((address, None, None))

                else:
                    new_method = contract_type.methods[data[:4]]
                    call_stack.append((address, contract_type, new_method))

            elif frame.op in ("RETURN", "REVERT"):
                addr, ct, function = call_stack[-1]
                if not ct:
                    continue

                # Find last line and make sure it is included.
                src_map = src_maps[str(ct.source_id)]
                start = list(src_map.keys())[0]
                for _pc in range(frame.pc, start, -1):
                    if _pc in src_map:
                        src_map[frame.pc] = src_map[_pc]
                        break

            addr, ct, function = call_stack[-1]
            if not ct or not ct.source_id:
                # Unable to add source lines without contract type.
                continue

            source_id = str(ct.source_id)
            if source_id in non_local_srcs:
                continue

            if source_id not in srcs:
                src = self.project_manager.lookup_path(Path(source_id))
                if not src:
                    non_local_srcs.add(source_id)
                    continue

                srcs[source_id] = src

            source = srcs[source_id]
            ext = source.suffix
            if ext not in EXTENSIONS and function and isinstance(function, MethodABI):
                sub_lines = self._get_line_trace_via_different_compiler(ext, trace, addr, function)
                lines = [*lines, *sub_lines]
                continue

            elif ext not in EXTENSIONS:
                continue

            if source_id not in src_maps:
                src_maps[source_id] = self.compiler_manager.get_pc_map(ct)

            if frame.op == "SSTORE" and "PUSH" in str(last_op) and push_src:
                src_map = {frame.pc: push_src}
            else:
                src_map = src_maps[source_id]

            last_op = frame.op
            if frame.pc not in src_map or not src_map[frame.pc]:
                if frame.op == "POP" and len(call_stack) >= 2:
                    # Check if popped back to last call.
                    penultimate = call_stack[-2]
                    if penultimate[1]:
                        sid = str(penultimate[1].source_id or "")  # Will never be empty.
                        if sid in src_maps:
                            source_id = sid
                            source = srcs[sid]
                            src_map = src_maps[sid]
                            addr, ct, function = penultimate
                            call_stack.pop()

                else:
                    # Unclear.
                    continue

            if "PUSH" in frame.op:
                if frame.pc in src_map:
                    push_src = src_map[frame.pc]

                continue

            src_mat = src_map[frame.pc]
            src_material = {}
            fmap = {}

            for line_no, line in src_mat.items():
                defining_f, is_sig = get_defining_method(source, line_no)
                if not is_sig:
                    src_material[line_no] = line
                else:
                    fmap[line_no] = defining_f

            if not src_material:
                continue

            if not len(lines) and function:
                # First set being added; no merging necessary.
                signature = _get_sig(function)
                node = LineTraceNode(source_id=source_id, method_id=signature, lines=src_material)
                lines.append(node)

            elif function:
                # Merge with previous line data.
                last_node = lines[-1]
                signature = _get_sig(function)
                if last_node.source_id == source_id and last_node.method_id == signature:
                    last_line_nos = list(last_node.lines.keys())
                    line_nos = list(src_material.keys())
                    first_new_no = line_nos[0]

                    if src_material == last_node.lines:
                        # Already covered.
                        continue

                    elif last_node.lines:
                        # Check if continuing from last node.
                        end_i = last_line_nos[0] + len(last_line_nos) + 1
                        if first_new_no in range(last_line_nos[0], end_i):
                            last_node.lines = {**last_node.lines, **src_material}
                            continue

                        else:
                            if first_new_no not in fmap:
                                fn, _ = get_defining_method(source, first_new_no)
                                if fn:
                                    fmap[first_new_no] = fn
                                else:
                                    continue

                            defining_f = fmap[first_new_no]
                            if not defining_f:
                                continue

                            if defining_f == _get_sig(function):
                                # Is same function but separated by comments or whitespace.
                                last_node.lines = {**last_node.lines, **src_material}
                                continue

                            # Check if popped from INTERNAL call
                            penultimate_ls = lines[-2]
                            if penultimate_ls and penultimate_ls.lines:
                                nos = list(penultimate_ls.lines.keys())
                                if first_new_no in range(nos[0], nos[0] + len(nos) + 1):
                                    penultimate_ls.lines = {**penultimate_ls.lines, **src_material}
                                    call_stack.pop()
                                    continue

                            # INTERNAL call from the same contract.
                            signature = defining_f
                            call_stack.append((addr, ct, signature))
                            node = LineTraceNode(
                                source_id=source_id, method_id=signature, lines=src_material
                            )
                            lines.append(node)

                else:
                    # Is a new jump.
                    signature = _get_sig(function)
                    node = LineTraceNode(
                        source_id=source_id, method_id=signature, lines=src_material
                    )
                    lines.append(node)

        if not lines:
            # At least add called method sig.
            # Happens on auto-getters.
            node = LineTraceNode(source_id=source_id, method_id=method_abi.signature, lines=[])
            lines.append(node)

        return lines

    def get_pc_map(self, contract_type: ContractType) -> PCMap:
        if not contract_type.pcmap:
            # Compiler does not support PC Map
            # TODO: Use alternative means
            return {}

        pc_map = contract_type.pcmap.parse()
        source_id = contract_type.source_id
        if not source_id:
            # Not a receipt made to a contract in the active project.
            return {}

        source = self.project_manager.lookup_path(Path(source_id))
        if not source:
            # Not a receipt made to a contract in the active project.
            return {}

        src_map: PCMap = {}
        content = source.read_text().splitlines()
        for pc, item in pc_map.items():
            if item.line_start is None:
                continue

            start = item.line_start
            if item.line_end is None:
                stop = start + 1
            else:
                stop = item.line_end + 1

            lines = {}
            for line_no in range(start, stop):
                line_index = line_no - 1  # Because starts at 0.
                if line_index < len(content) and content[line_index]:
                    lines[line_no] = content[line_index]

            if lines:
                src_map[pc] = lines

        return src_map

    def get_coverage_profile(self, contract: ContractType) -> CoverageItem:
        source_id = contract.source_id
        if not source_id:
            raise ValueError("Unable to get coverage profile - missing source ID")

        source_path = self.config_manager.contracts_folder / source_id
        if not source_path.is_file():
            raise FileNotFoundError(str(source_path))

        item = CoverageItem()
        pc_map = self.compiler_manager.get_pc_map(contract)
        for line_numbers in pc_map.values():
            for line_no in line_numbers:
                item.lines.add(line_no)

        # Add external methods.
        for method in contract.methods:
            if not method.name:
                continue

            item.functions.add(method.signature)

        # Add internal methods.
        lines = source_path.read_text().splitlines()
        for idx, line in enumerate(lines):
            if line.startswith("@internal"):
                start = idx + 1
                for idx_2, sub_line in enumerate(lines[start:]):
                    if not sub_line.startswith("def "):
                        continue

                    signature, _ = get_defining_method(source_path, idx + 1)
                    if signature:
                        item.functions.add(signature)

        return item

    def _get_line_trace_via_different_compiler(
        self,
        ext: str,
        trace: Iterator[TraceFrame],
        contract_address: AddressType,
        method_abi: MethodABI,
    ) -> List[LineTraceNode]:
        if ext not in self.compiler_manager.registered_compilers:
            return []

        # Potentially got here from a sub-call.
        # Attempt to use another compiler
        compiler = self.compiler_manager.registered_compilers[ext]
        try:
            return compiler.get_line_trace(trace, contract_address, method_abi)
        except APINotImplementedError:
            return []

    def _get_compiler_arguments(self, version_map: Dict, base_path: Path) -> Dict[Version, Dict]:
        base_path = base_path or self.project_manager.contracts_folder
        arguments_map = {}
        vyper_bin = shutil.which("vyper")
        for vyper_version, source_paths in version_map.items():
            bin_arg = vyper_bin if vyper_version is self.package_version else None
            arguments_map[vyper_version] = {
                "base_path": str(base_path),
                "evm_version": self.evm_version,
                "vyper_version": str(vyper_version),
                "vyper_binary": bin_arg,
            }

        return arguments_map


def _safe_append(data: Dict, version: Union[Version, NpmSpec], paths: Union[Path, Set]):
    if isinstance(paths, Path):
        paths = {paths}
    if version in data:
        data[version] = data[version].union(paths)
    else:
        data[version] = paths


def get_defining_method(source: Path, line_no: int) -> Tuple[Optional[str], bool]:
    """
    Returns the method signature and a bool that is True when the line is
    part of the signature.
    """
    content = source.read_text().splitlines()
    if not content:
        return "", False

    line_index = line_no - 1
    line = _strip_comments(content[line_index])

    # Line is literally a `def` statement.
    if line.startswith("def "):
        # Gather rest of signature.
        signature, _ = _build_signature(line, line_index, content)
        return signature, True

    # Find defining method.
    for i in range(line_index - 1, -1, -1):
        sub_line = _strip_comments(content[i])
        if sub_line.startswith("def "):
            signature, indices = _build_signature(sub_line, i, content)
            return signature, line_index in indices

    return None, False


def _build_signature(def_line: str, line_index: int, content: List[str]) -> Tuple[str, List[int]]:
    signature = _strip_comments(def_line.replace("def ", ""))
    if signature.endswith(":"):
        return signature.rstrip(":"), [line_index]

    start = line_index + 1
    line_indices = [line_index]
    for idx, sub_line in enumerate(content[start:]):
        sub_line = _strip_comments(sub_line).lstrip()
        if signature.endswith(","):
            sub_line = f" {sub_line}"

        signature += sub_line.rstrip(":")
        line_indices.append(idx + start)
        if sub_line.endswith(":"):
            return signature, line_indices

    # Shouldn't get here.
    raise ValueError("End of signature not found.")


def _strip_comments(line: str) -> str:
    return line.split("#")[0].rstrip()


def _get_sig(item: Union[MethodABI, str, ABIType]) -> str:
    """
    Similar to signature from ethpm_types, but in Vyper format.
    """
    if isinstance(item, ABIType):
        return f"{item.name}: {item.type}"

    elif isinstance(item, MethodABI):
        input_args = ", ".join(_get_sig(i) for i in item.inputs)
        output_args = ""

        if item.outputs:
            output_args = " -> "
            if len(item.outputs) > 1:
                output_args += "(" + ", ".join(o.canonical_type for o in item.outputs) + ")"

            else:
                output_args += item.outputs[0].canonical_type

        return f"{item.name}({input_args}){output_args}"

    else:
        return str(item)
