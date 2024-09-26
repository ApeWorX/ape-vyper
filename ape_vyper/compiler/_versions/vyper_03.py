from typing import Any

from packaging.version import Version

from ape_vyper._utils import Optimization, get_legacy_pcmap, get_pcmap
from ape_vyper.compiler._versions.base import BaseVyperCompiler


class Vyper03Compiler(BaseVyperCompiler):
    """
    Compiler for Vyper>=0.3.0,<0.4.
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
