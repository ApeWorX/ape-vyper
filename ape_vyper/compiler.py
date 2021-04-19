from pathlib import Path

import vvm  # type: ignore
from ape.api.compiler import CompilerAPI
from ape.types import Bytecode, ContractType


class VyperCompiler(CompilerAPI):
    @property
    def name(self) -> str:
        return "vyper"

    def compile(self, contract_filepath: Path) -> ContractType:
        result = vvm.compile_source(contract_filepath.read_text())

        result = result["<stdin>"]
        return ContractType(
            # NOTE: Vyper doesn't have internal contract type declarations, so use filename
            contractName=contract_filepath.stem,
            sourceId=contract_filepath,
            deploymentBytecode=Bytecode(result["bytecode"]),  # type: ignore
            runtimeBytecode=Bytecode(result["bytecode_runtime"]),  # type: ignore
            abi=result["abi"],
            userdoc=result["userdoc"],
            devdoc=result["devdoc"],
        )
