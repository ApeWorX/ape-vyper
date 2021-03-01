from typing import Dict
from pathlib import Path

from ape.plugins.compiler_api import CompilerAPI

import click


class VyperCompiler(CompilerAPI):
    @property
    def name(self) -> str:
        return "vyper"

    @classmethod
    def extension(self) -> str:
        return ".vy"

    def compile(self, contracts_folder: Path) -> Dict:
        click.echo(f"vyper plugin compile called, compiling {contracts_folder}")
        click.echo("vyper compilation finished")
