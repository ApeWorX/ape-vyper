from collections.abc import Iterable
from pathlib import Path
from typing import Optional

from ape.managers import ProjectManager
from ape.utils import get_relative_path
from ethpm_types import SourceMap
from packaging.version import Version

from ape_vyper._utils import Optimization
from ape_vyper.compiler._versions.base import BaseVyperCompiler


class Vyper04Compiler(BaseVyperCompiler):
    """
    Compiler for Vyper>=0.4.0.
    """

    def _get_sources_dictionary(
        self, source_ids: Iterable[str], project: Optional[ProjectManager] = None, **kwargs
    ) -> dict[str, dict]:
        pm = project or self.local_project
        if not source_ids:
            return {}

        import_map = kwargs.get("import_map", {})
        src_dict = {}
        use_absolute_paths = kwargs.get("use_absolute_paths", False)

        for source_id in source_ids:
            path = Path(source_id)
            if path.is_absolute() and path.is_file():
                content = path.read_text()
            elif (pm.path / source_id).is_file():
                content = (pm.path / source_id).read_text()
            else:
                continue

            if use_absolute_paths:
                source_id = str(pm.path / source_id)

            src_dict[source_id] = {"content": content}

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

                    else:
                        # Is from a dependency.
                        specified = {d.name: d for d in pm.dependencies.specified}
                        is_site_packages = "site-packages" in f"{imp_path}"
                        for parent in imp_path.parents:
                            if parent.name == "site-packages":
                                is_site_packages = True
                                src_id = f"{get_relative_path(imp_path, parent)}"
                                break

                            elif not is_site_packages and parent.name in specified:
                                dependency = specified[parent.name]
                                src_id = f"{imp_path}"
                                imp_path = dependency.project.path / imp_path
                                if imp_path.is_file():
                                    break

                        # Likely from a dependency. Exclude absolute prefixes so Vyper
                        # knows what to do.
                        if imp_path.is_file() and (
                            not Path(src_id).is_absolute() or is_site_packages
                        ):
                            src_dict[src_id] = {"content": imp_path.read_text(encoding="utf8")}

        return src_dict

    def _get_compile_kwargs(
        self, vyper_version: Version, compiler_data: dict, project: Optional[ProjectManager] = None
    ) -> dict:
        return self._get_base_compile_kwargs(vyper_version, compiler_data)

    def _get_default_optimization(self, vyper_version: Version) -> Optimization:
        return "gas"

    def _parse_source_map(self, raw_source_map: dict) -> SourceMap:
        return SourceMap(root=raw_source_map["pc_pos_map_compressed"])
