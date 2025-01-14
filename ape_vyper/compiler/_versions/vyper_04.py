import os
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import vvm
from ape.utils import get_full_extension, get_relative_path
from ethpm_types import SourceMap
from ethpm_types.source import Content
from vvm.exceptions import VyperError

from ape_vyper._utils import FileType, Optimization
from ape_vyper.compiler._versions.base import BaseVyperCompiler
from ape_vyper.compiler._versions.utils import output_details
from ape_vyper.exceptions import VyperCompileError
from ape_vyper.imports import ImportMap

if TYPE_CHECKING:
    from ape.managers.project import ProjectManager
    from packaging.version import Version


class Vyper04Compiler(BaseVyperCompiler):
    """
    Compiler for Vyper>=0.4.0.
    """

    def get_import_remapping(self, project: Optional["ProjectManager"] = None) -> dict[str, dict]:
        # Import remappings are not used in 0.4.
        # You always import via module or package name.
        return {}

    def get_settings(
        self,
        version: "Version",
        source_paths: Iterable[Path],
        compiler_data: dict,
        project: Optional["ProjectManager"] = None,
    ) -> dict:
        pm = project or self.local_project

        enable_decimals = self.api.get_config(project=pm).enable_decimals
        if enable_decimals is None:
            enable_decimals = False

        settings = super().get_settings(version, source_paths, compiler_data, project=pm)
        for settings_set in settings.values():
            settings_set["enable_decimals"] = enable_decimals

        return settings

    def _get_sources_dictionary(
        self, source_ids: Iterable[str], project: Optional["ProjectManager"] = None, **kwargs
    ) -> dict:
        pm = project or self.local_project
        if not source_ids:
            return {}

        import_map: ImportMap = kwargs["import_map"]
        src_dict = {}

        for source_id in source_ids:
            path = Path(source_id)

            if path.is_absolute():
                abs_path = path
                rel_path = get_relative_path(abs_path, pm.path)
            else:
                abs_path = pm.path / source_id
                rel_path = path

            if not abs_path.is_file():
                continue

            source_id = f"{rel_path}"

            # Vyper 0.4 uses the normal vyper compiler, and thus
            # does not to make a content-dictionary of all the sources
            # (just uses paths).
            src_dict[source_id] = abs_path

            if imports := import_map.get(abs_path):
                for imp in imports:
                    if imp.source_id in src_dict:
                        continue
                    elif not (imp_path := imp.path):
                        continue
                    elif not imp_path.is_file():
                        continue

                    src_dict[imp.source_id] = imp_path

        return src_dict

    def _get_compile_kwargs(
        self,
        vyper_version: "Version",
        compiler_data: dict,
        project: Optional["ProjectManager"] = None,
    ) -> dict:
        pm = project or self.local_project
        compile_kwargs = self._get_base_compile_kwargs(vyper_version, compiler_data)
        compile_kwargs["output_format"] = ",".join(
            [
                "bytecode",
                "bytecode_runtime",
                "abi_python",
                "source_map",
                "userdoc",
                "devdoc",
                "ast",
                "opcodes",
            ]
        )
        compile_kwargs["evm_version"] = compiler_data.get("evmVersion")
        compile_kwargs["base_path"] = pm.path
        return compile_kwargs

    def _get_default_optimization(self, vyper_version: "Version") -> Optimization:
        return "gas"

    def _parse_source_map(self, raw_source_map: dict) -> SourceMap:
        return SourceMap(root=raw_source_map["pc_pos_map_compressed"])

    def _get_selection_dictionary(
        self, selection: Iterable[str], project: Optional["ProjectManager"] = None, **kwargs
    ) -> dict:
        pm = project or self.local_project
        return {
            s: ["*"]
            for s in selection
            if ((pm.path / s).is_file())
            and f"interfaces{os.path.sep}" not in s
            and get_full_extension(pm.path / s) != FileType.INTERFACE
        }

    def compile(
        self,
        vyper_version: "Version",
        settings: dict,
        import_map: "ImportMap",
        compiler_data: dict,
        project: Optional["ProjectManager"] = None,
    ):
        pm = project or self.local_project
        for settings_key, settings_set in settings.items():
            if not (output_selection := settings_set.get("outputSelection", {})):
                continue

            src_dict = self._get_sources_dictionary(
                output_selection,
                project=pm,
                import_map=import_map,
            )

            # Output compiler details.
            output_details(*output_selection.keys(), version=vyper_version)

            comp_kwargs = self._get_compile_kwargs(vyper_version, compiler_data, project=pm)

            here = Path.cwd()
            if pm.path != here:
                os.chdir(pm.path)
            try:
                result = vvm.compile_files(src_dict.values(), **comp_kwargs)
            except VyperError as err:
                raise VyperCompileError(err) from err
            finally:
                if Path.cwd() != here:
                    os.chdir(here)

            breakpoint()
            # for source_id, output_items in result["contracts"].items():
            #     content = Content.model_validate(src_dict[source_id].get("content", "")).root
            #     for name, output in output_items.items():
            #         # De-compress source map to get PC POS map.
            #         ast = self._parse_ast(result["sources"][source_id]["ast"])
            #         evm = output["evm"]
            #         bytecode = evm["deployedBytecode"]
            #         opcodes = bytecode["opcodes"].split(" ")
            #         compressed_src_map = self._parse_source_map(bytecode["sourceMap"])
            #         src_map = list(compressed_src_map.parse())[1:]
            #         pcmap = self._get_pcmap(vyper_version, ast, src_map, opcodes, bytecode)
            #
            #         # Find content-specified dev messages.
            #         dev_messages = {}
            #         for line_no, line in content.items():
            #             if match := re.search(DEV_MSG_PATTERN, line):
            #                 dev_messages[line_no] = match.group(1).strip()
            #
            #         source_id_path = Path(source_id)
            #         if source_id_path.is_absolute():
            #             final_source_id = f"{get_relative_path(Path(source_id), pm.path)}"
            #         else:
            #             final_source_id = source_id
            #
            #         contract_type = ContractType.model_validate(
            #             {
            #                 "ast": ast,
            #                 "contractName": name,
            #                 "sourceId": final_source_id,
            #                 "deploymentBytecode": {"bytecode": evm["bytecode"]["object"]},
            #                 "runtimeBytecode": {"bytecode": bytecode["object"]},
            #                 "abi": output["abi"],
            #                 "sourcemap": compressed_src_map,
            #                 "pcmap": pcmap,
            #                 "userdoc": output["userdoc"],
            #                 "devdoc": output["devdoc"],
            #                 "dev_messages": dev_messages,
            #             }
            #         )
            #         yield contract_type, settings_key
