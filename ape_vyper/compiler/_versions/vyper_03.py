import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Optional

from ape.managers import ProjectManager
from ape.utils import get_full_extension
from packaging.version import Version

from ape_vyper._utils import FileType, Optimization, get_legacy_pcmap, get_pcmap
from ape_vyper.compiler._versions.base import BaseVyperCompiler


class Vyper03Compiler(BaseVyperCompiler):
    """
    Compiler for Vyper>=0.3.3,<0.4.
    """

    def _get_pcmap(
        self, vyper_version: Version, ast: Any, src_map: list, opcodes: list[str], bytecode: dict
    ):
        return (
            get_legacy_pcmap(ast, src_map, opcodes)
            if vyper_version <= Version("0.3.7")
            else get_pcmap(bytecode)
        )

    def _get_default_optimization(self, vyper_version: Version) -> Optimization:
        return True if vyper_version < Version("0.3.10") else "gas"

    def _get_selection_dictionary(
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
