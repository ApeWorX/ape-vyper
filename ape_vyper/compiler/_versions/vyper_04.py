import json
import os
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from ape.logging import logger
from ape.utils import get_full_extension, get_relative_path
from ape.utils.os import clean_path
from ethpm_types import ContractType, PCMap
from ethpm_types.source import Content
from vvm.install import get_executable  # type: ignore

from ape_vyper._utils import FileType, Optimization, compile_files
from ape_vyper.compiler._versions.base import BaseVyperCompiler
from ape_vyper.compiler._versions.utils import map_dev_messages, output_details
from ape_vyper.config import VYPER_04_OUTPUT_FORMAT
from ape_vyper.exceptions import VyperCompileError, VyperError

if TYPE_CHECKING:
    from ape.managers.project import ProjectManager
    from packaging.version import Version

    from ape_vyper.imports import ImportMap


class Vyper04Compiler(BaseVyperCompiler):
    """
    Compiler for Vyper>=0.4.0.
    """

    def get_output_format(self, project: "ProjectManager | None" = None) -> list[str]:
        pm = project or self.local_project
        return pm.config.vyper.output_format or VYPER_04_OUTPUT_FORMAT

    def get_import_remapping(self, project: "ProjectManager | None" = None) -> dict[str, dict]:
        # Import remappings are not used in 0.4.
        # You always import via module or package name.
        return {}

    def get_settings(
        self,
        version: "Version",
        source_paths: Iterable[Path],
        project: "ProjectManager | None" = None,
    ) -> dict:
        pm = project or self.local_project

        enable_decimals = self.api.get_config(project=pm).enable_decimals
        if enable_decimals is None:
            enable_decimals = False

        settings = super().get_settings(version, source_paths, project=pm)
        for settings_set in settings.values():
            settings_set["enable_decimals"] = enable_decimals

        return settings

    def _get_sources_dictionary(
        self, source_ids: Iterable[str], project: "ProjectManager | None" = None, **kwargs
    ) -> dict:
        pm = project or self.local_project
        if not source_ids:
            return {}

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

        return src_dict

    def _get_default_optimization(self, vyper_version: "Version") -> Optimization:
        return "gas"

    def _get_selection_dictionary(
        self, selection: Iterable[str], project: "ProjectManager | None" = None, **kwargs
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
        project: "ProjectManager | None" = None,
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

            comp_kwargs = {
                "evm_version": self.get_evm_version(vyper_version),
                "output_format": self.get_output_format(project=pm),
                "enable_decimals": settings_set.get("enable_decimals", False),
            }

            if self.api.package_version == vyper_version:
                if path_str := shutil.which("vyper"):
                    binary = Path(path_str)
                else:
                    # Last attempt - but this state is unlikely (cli failure in vyper?)
                    binary = get_executable(version=vyper_version)
            else:
                binary = get_executable(version=vyper_version)

            files = [Path(p) for p in src_dict]

            if "solc_json" in comp_kwargs.get("output_format", []):
                # 'solc_json' output format does not work with other formats.
                # So, we handle it separately.
                comp_kwargs["output_format"] = [
                    f for f in comp_kwargs["output_format"] if f != "solc_json"
                ]
                solc_json_comp_kwargs = {
                    k: v for k, v in comp_kwargs.items() if k != "output_format"
                }
                solc_json_comp_kwargs["output_format"] = ["solc_json"]
                try:
                    result = compile_files(binary, files, pm.path, **solc_json_comp_kwargs)
                except VyperError as err:
                    raise VyperCompileError(err) from err

                for source_id, output_items in result.items():
                    self._output_solc_json(source_id, output_items["solc_json"], project=pm)

            result = {}
            with pm.within_project_path():
                try:
                    result = compile_files(binary, files, pm.path, **comp_kwargs)
                except VyperError as err:
                    raise VyperCompileError(err) from err

            for source_id, output_items in result.items():
                content = Content(root=src_dict[source_id].read_text(encoding="utf-8"))

                if "ast" in output_items:
                    # De-compress source map to get PC POS map.
                    ast_dict = json.loads(output_items["ast"])["ast"]
                    ast = self._parse_ast(ast_dict, content)
                else:
                    ast = None

                bytecode = output_items.get("bytecode_runtime")

                if "source_map" in output_items:
                    source_map = json.loads(output_items["source_map"])
                    pcmap = PCMap.model_validate(source_map["pc_pos_map"])
                else:
                    pcmap = None

                # Find content-specified dev messages.
                dev_messages = map_dev_messages(content.root)

                source_id_path = Path(source_id)
                if source_id_path.is_absolute():
                    final_source_id = f"{get_relative_path(Path(source_id), pm.path)}"
                else:
                    final_source_id = source_id

                contract_type = ContractType.model_validate(
                    {
                        "ast": ast,
                        "contractName": f"{Path(final_source_id).stem}",
                        "sourceId": final_source_id,
                        "deploymentBytecode": (
                            {"bytecode": output_items["bytecode"]}
                            if "bytecode" in output_items
                            else {}
                        ),
                        "runtimeBytecode": {"bytecode": bytecode} if bytecode else {},
                        "abi": json.loads(output_items["abi"]) if "abi" in output_items else None,
                        "sourcemap": (
                            output_items["source_map"] if "source_map" in output_items else None
                        ),
                        "pcmap": pcmap,
                        "userdoc": (
                            json.loads(output_items["userdoc"])
                            if "userdoc" in output_items
                            else None
                        ),
                        "devdoc": (
                            json.loads(output_items["devdoc"]) if "devdoc" in output_items else None
                        ),
                        "dev_messages": dev_messages,
                    }
                )
                yield contract_type, settings_key

    def _output_solc_json(
        self, source_id: str, solc_json: str, project: "ProjectManager | None" = None
    ):
        pm = project or self.local_project
        output_path = pm.manifest_path.parent
        source_path = Path(source_id)
        output_file = output_path / f"{source_path.stem}_solc.json"
        logger.info(
            f"Writing 'solc_json' output for source {clean_path(Path(source_id))} "
            f"to {clean_path(output_file)}"
        )
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.unlink(missing_ok=True)
        output_file.write_text(solc_json, encoding="utf-8")
