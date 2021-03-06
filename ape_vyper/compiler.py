import os
from typing import Dict
from pathlib import Path

from ape.plugins.compiler_api import CompilerAPI

import click
import vvm


class VyperCompiler(CompilerAPI):
    @property
    def name(self) -> str:
        return "vyper"

    @classmethod
    def extension(self) -> str:
        return ".vy"

    # convert dict to class defined in ape core
    # arguments are folder, and compiler settings (from config)
    def compile(self, contracts_folder: Path) -> Dict:
        click.echo(f"compiling {contracts_folder}")

        contracts = [os.path.join(contracts_folder, c) for c in os.listdir(contracts_folder) if c.endswith(".vy")]

        result = vvm.compile_files(contracts)

        click.echo("vyper compilation finished")

        return result
