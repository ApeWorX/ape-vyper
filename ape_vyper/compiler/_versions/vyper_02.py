from typing import TYPE_CHECKING, Any

from ape_vyper._utils import get_legacy_pcmap
from ape_vyper.compiler._versions.base import BaseVyperCompiler

if TYPE_CHECKING:
    from packaging.version import Version


class Vyper02Compiler(BaseVyperCompiler):
    """
    Compiler for Vyper>=0.2.7,<0.3.
    """

    DEFAULT_OPTIMIZATION = True

    def _get_pcmap(
        self,
        vyper_version: "Version",
        ast: Any,
        src_map: list,
        opcodes: list[str],
        bytecode: dict,
    ):
        return get_legacy_pcmap(ast, src_map, opcodes)
