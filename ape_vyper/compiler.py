import os
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Set, Union, cast

import vvm  # type: ignore
from ape.api import PluginConfig, ReceiptAPI
from ape.api.compiler import CompilerAPI
from ape.exceptions import APINotImplementedError
from ape.logging import logger
from ape.types import ContractType, LineTraceNode, PCMap
from ape.utils import cached_property, get_relative_path
from semantic_version import NpmSpec, Version  # type: ignore

from .exceptions import VyperCompileError, VyperInstallError


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
        self, receipt: ReceiptAPI, contract_type: ContractType
    ) -> List[LineTraceNode]:
        source_id = contract_type.source_id
        if not source_id:
            # Likely not a local contract.
            return []

        source = self.project_manager.lookup_path(Path(source_id))
        if not source:
            # Likely not a local contract.
            return []

        ext = Path(source_id).suffix
        if ext != ".vy":
            return self._get_line_trace_via_different_compiler(ext, receipt, contract_type)

        call_tree = receipt.call_tree
        if not call_tree:
            return []

        lines: List[LineTraceNode] = []

        # src_id -> PC -> line_no -> line_str
        root_src_maps: Dict[str, Dict[int, Dict[int, str]]] = {}

        last_depth = 1
        current_call = call_tree.copy()
        previous_call = None
        for trace in receipt.trace:
            if "PUSH" in trace.op:
                # Ignore PUSH opcodes to attempt to preserve a more-human
                # friendly ordering of the lines. Else, things seem out-of
                # -order, maybe for compiler reasons.
                continue

            elif trace.depth > last_depth and current_call.calls:
                # Made a call
                previous_call = current_call.copy()
                current_call = current_call.calls.pop(0)

            elif trace.depth > last_depth:
                # Not sure if is possible.
                continue

            elif trace.depth < last_depth:
                # Popped a call
                current_call = previous_call

            last_depth = trace.depth

            # Find the address to get the contract type.
            if call_tree.contract_id.startswith("0x"):
                address = call_tree.contract_id
            else:
                # Handle enriched call tree.
                address_bytes = current_call.raw["address"]
                address = self.provider.network.ecosystem.decode_address(address_bytes)

            contract_type = self.chain_manager.contracts.get(address)
            if not contract_type or not contract_type.source_id:
                # Unable to add source lines without contract type.
                continue

            source_id = str(contract_type.source_id)
            ext = Path(source_id).suffix
            if ext != ".vy":
                return self._get_line_trace_via_different_compiler(ext, receipt, contract_type)
            elif source_id in root_src_maps:
                src_map = root_src_maps[source_id]
            else:
                # Cache for accessing next time faster.
                src_map = self.compiler_manager.get_pc_map(contract_type)
                root_src_maps[source_id] = src_map

            if trace.pc not in src_map or not src_map[trace.pc]:
                continue

            src_material = src_map[trace.pc]

            # Note the method called.
            method_id = current_call.enrich(in_place=False).method_id

            if not len(lines):
                # First set being added; no merging necessary.
                node = LineTraceNode(source_id=source_id, method_id=method_id, lines=src_material)
                lines.append(node)

            else:
                # Merge with previous line data.
                last_node = lines[-1]
                if last_node.source_id == source_id and last_node.method_id == method_id:
                    if src_material == last_node.lines:
                        # Already covered.
                        continue

                    elif last_node.lines:
                        # Check if continuing from last node.
                        last_lines = list(last_node.lines.keys())
                        first_new_line_num = list(src_material.keys())[0]
                        if first_new_line_num in range(
                            last_lines[0], last_lines[0] + len(last_lines) + 1
                        ):
                            last_node.lines = {**last_node.lines, **src_material}
                            continue
                else:
                    # Is a new jump.
                    node = LineTraceNode(
                        source_id=source_id, method_id=method_id, lines=src_material
                    )
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
                if line_no < len(content) and content[line_no]:
                    lines[line_no] = content[line_no]

            if lines:
                src_map[pc] = lines

        return src_map

    def _get_line_trace_via_different_compiler(
        self, ext: str, receipt: ReceiptAPI, contract_type: ContractType
    ) -> List[LineTraceNode]:
        if ext not in self.compiler_manager.registered_compilers:
            return []

        # Potentially got here from a sub-call.
        # Attempt to use another compiler
        compiler = self.compiler_manager.registered_compilers[ext]
        try:
            return compiler.get_line_trace(receipt, contract_type)
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
