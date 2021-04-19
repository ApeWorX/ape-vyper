from pathlib import Path

import click
import vvm  # type: ignore
from ape.api.compiler import CompilerAPI
from ape.types import Bytecode, ContractType


class VyperCompiler(CompilerAPI):
    @property
    def name(self) -> str:
        return "vyper"

    def compile(self, contract_filepath: Path) -> ContractType:
        click.echo(f"Compiling '{contract_filepath}'")

        result = vvm.compile_source(contract_filepath.read_text())

        result = result["<stdin>"]
        return ContractType(
            contractName=contract_filepath.name,
            sourceId=contract_filepath,
            deploymentBytecode=Bytecode(result["bytecode"]),  # type: ignore
            runtimeBytecode=Bytecode(result["bytecode_runtime"]),  # type: ignore
            abi=result["abi"],
            userdoc=result["userdoc"],
            devdoc=result["devdoc"],
        )
