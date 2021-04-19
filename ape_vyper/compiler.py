import re
from pathlib import Path
from typing import List, Optional

import vvm  # type: ignore
from ape.api.compiler import CompilerAPI
from ape.types import Bytecode, ContractType
from ape.utils import cached_property
from semantic_version import NpmSpec, Version  # type: ignore


def get_pragma_spec(source: str) -> Optional[NpmSpec]:
    """
    Extracts pragma information from Vyper source code.
    Args:
        source: Vyper source code
    Returns: NpmSpec object or None, if no valid pragma is found
    """
    pragma_match = next(re.finditer(r"(?:\n|^)\s*#\s*@version\s*([^\n]*)", source), None)
    if pragma_match is None:
        return None  # Try compiling with latest

    pragma_string = pragma_match.groups()[0]
    pragma_string = " ".join(pragma_string.split())

    try:
        return NpmSpec(pragma_string)

    except ValueError:
        return None


class VyperCompiler(CompilerAPI):
    @property
    def name(self) -> str:
        return "vyper"

    @cached_property
    def package_version(self) -> Optional[Version]:
        try:
            import vyper  # type: ignore

            return Version(vyper.__version__)

        except ImportError:
            return None

    @cached_property
    def available_versions(self) -> List[Version]:
        # NOTE: Package version should already be included in available versions
        return vvm.get_installable_vyper_versions()

    @property
    def installed_versions(self) -> List[Version]:
        package_version = self.package_version
        package_version = [package_version] if package_version else []
        return package_version + vvm.get_installed_vyper_versions()

    def compile(self, contract_filepath: Path) -> ContractType:
        source = contract_filepath.read_text()

        # Make sure we have the compiler available to compile this
        version_spec = get_pragma_spec(source)
        if version_spec:
            version = version_spec.select(self.available_versions)

            if version not in self.installed_versions:
                vvm.install_vyper(version)

        elif len(self.installed_versions) == 0:
            vvm.install_vyper(self.available_versions[0])
            version = self.installed_versions[0]

        else:
            version = self.installed_versions[0]

        # Actually do the compilation
        result = vvm.compile_source(source)
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
