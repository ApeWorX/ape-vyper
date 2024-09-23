from collections.abc import Iterable
from pathlib import Path
from typing import Optional

from ape.managers import ProjectManager
from ape.utils import get_relative_path
from ethpm_types import SourceMap
from packaging.version import Version

from ape_vyper._utils import Optimization
from ape_vyper.compiler._versions.base import BaseVyperCompiler
from ape_vyper.imports import ImportMap


class Vyper04Compiler(BaseVyperCompiler):
    """
    Compiler for Vyper>=0.4.0.
    """

    @property
    def import_remapping(self) -> dict[str, dict]:
        # Import remappings are not used in 0.4.
        # You always import via module or package name.
        return {}

    def _get_sources_dictionary(
        self, source_ids: Iterable[str], project: Optional[ProjectManager] = None, **kwargs
    ) -> dict[str, dict]:
        pm = project or self.local_project
        if not source_ids:
            return {}

        import_map: ImportMap = kwargs["import_map"]
        src_dict = {}
        use_absolute_paths = kwargs.get("use_absolute_paths", False)

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

            source_id = f"{abs_path}" if use_absolute_paths else f"{rel_path}"
            content = abs_path.read_text(encoding="utf8")
            src_dict[source_id] = {"content": content}

            if imports := import_map.get(abs_path):
                for imp in imports:
                    if imp.source_id in src_dict:
                        continue
                    elif not (imp_path := imp.path):
                        continue
                    elif not imp_path.is_file():
                        continue

                    src_dict[imp.source_id] = {"content": imp_path.read_text(encoding="utf8")}

        return src_dict

    def _get_compile_kwargs(
        self, vyper_version: Version, compiler_data: dict, project: Optional[ProjectManager] = None
    ) -> dict:
        return self._get_base_compile_kwargs(vyper_version, compiler_data)

    def _get_default_optimization(self, vyper_version: Version) -> Optimization:
        return "gas"

    def _parse_source_map(self, raw_source_map: dict) -> SourceMap:
        return SourceMap(root=raw_source_map["pc_pos_map_compressed"])
