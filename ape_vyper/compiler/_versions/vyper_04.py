import json
import os
from collections.abc import Iterable
from pathlib import Path
from site import getsitepackages
from typing import TYPE_CHECKING, Optional

from ape.utils import get_full_extension, get_relative_path
from ethpm_types import ContractType, PCMap
from ethpm_types.source import Content
from vvm.install import get_executable  # type: ignore

from ape_vyper._utils import FileType, Optimization, compile_files
from ape_vyper.compiler._versions.base import BaseVyperCompiler
from ape_vyper.compiler._versions.utils import map_dev_messages, output_details
from ape_vyper.config import VYPER_04_OUTPUT_FORMAT
from ape_vyper.exceptions import VyperCompileError, VyperError
from ape_vyper.imports import ImportMap

if TYPE_CHECKING:
    from ape.managers.project import ProjectManager
    from packaging.version import Version


class Vyper04Compiler(BaseVyperCompiler):
    """
    Compiler for Vyper>=0.4.0.
    """

    @property
    def output_format(self) -> list[str]:
        return self.config.output_format or VYPER_04_OUTPUT_FORMAT

    def get_import_remapping(self, project: Optional["ProjectManager"] = None) -> dict[str, dict]:
        # Import remappings are not used in 0.4.
        # You always import via module or package name.
        return {}

    def get_settings(
        self,
        version: "Version",
        source_paths: Iterable[Path],
        project: Optional["ProjectManager"] = None,
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
        self, source_ids: Iterable[str], project: Optional["ProjectManager"] = None, **kwargs
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

            comp_kwargs = {
                "evm_version": self.get_evm_version(vyper_version),
                "output_format": self.config.output_format or VYPER_04_OUTPUT_FORMAT,
                "additional_paths": [*getsitepackages()],
                "enable_decimals": settings.get("enable_decimals", False),
            }

            here = Path.cwd()
            if pm.path != here:
                os.chdir(pm.path)

            binary = get_executable(version=vyper_version)

            try:
                result = compile_files(binary, [Path(p) for p in src_dict], pm.path, **comp_kwargs)
            except VyperError as err:
                raise VyperCompileError(err) from err

            finally:
                if Path.cwd() != here:
                    os.chdir(here)

            for source_id, output_items in result.items():
                content = Content(root=src_dict[source_id].read_text(encoding="utf-8"))
                # De-compress source map to get PC POS map.
                ast_dict = json.loads(output_items["ast"])["ast"]
                ast = self._parse_ast(ast_dict, content)
                bytecode = output_items["bytecode_runtime"]

                source_map = json.loads(output_items["source_map"])
                pcmap = PCMap.model_validate(source_map["pc_pos_map"])

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
                        "deploymentBytecode": {"bytecode": output_items["bytecode"]},
                        "runtimeBytecode": {"bytecode": bytecode},
                        "abi": json.loads(output_items["abi"]),
                        "sourcemap": output_items["source_map"],
                        "pcmap": pcmap,
                        "userdoc": json.loads(output_items["userdoc"]),
                        "devdoc": json.loads(output_items["devdoc"]),
                        "dev_messages": dev_messages,
                    }
                )
                yield contract_type, settings_key
