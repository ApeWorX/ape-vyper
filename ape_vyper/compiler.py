import re
import shutil
from pathlib import Path
from typing import List, Optional, Set

import vvm  # type: ignore
from ape.api import ConfigDict
from ape.api.compiler import CompilerAPI
from ape.types import ContractType
from ape.utils import cached_property, get_relative_path
from semantic_version import NpmSpec, Version  # type: ignore

from .exceptions import VyperCompileError, VyperInstallError


def _install_vyper(version: Version):
    try:
        vvm.install_vyper(version, show_progress=True)
    except Exception as err:
        raise VyperInstallError(f"Unable to install Vyper version: '{version}'.") from err


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
    config: ConfigDict = ConfigDict()

    @property
    def name(self) -> str:
        return "vyper"

    def get_versions(self, all_paths: List[Path]) -> Set[str]:
        versions = set()
        for path in all_paths:
            source = path.read_text()

            # Make sure we have the compiler available to compile this
            version_spec = get_pragma_spec(source)
            if version_spec:
                versions.add(str(version_spec.select(self.available_versions)))

        return versions

    @cached_property
    def package_version(self) -> Optional[Version]:
        try:
            import vyper  # type: ignore

            # Strip off parts from source-installation
            version = Version.coerce(vyper.__version__)
            return Version(major=version.major, minor=version.minor, patch=version.patch)

        except ImportError:
            return None

    @cached_property
    def available_versions(self) -> List[Version]:
        # NOTE: Package version should already be included in available versions
        return vvm.get_installable_vyper_versions()

    @property
    def installed_versions(self) -> List[Version]:
        # doing this so it prefers package version - try debugging here
        package_version = self.package_version
        package_version = [package_version] if package_version else []
        # currently package version is [] this should be ok
        return package_version + vvm.get_installed_vyper_versions()

    @cached_property
    def vyper_json(self):
        from vyper.cli import vyper_json  # type: ignore

        # step through this function to debug
        return vyper_json

    def compile(
        self, contract_filepaths: List[Path], base_path: Optional[Path] = None
    ) -> List[ContractType]:
        # todo: move this to vvm
        contract_types = []
        for path in contract_filepaths:
            source = path.read_text()
            pragma_spec = get_pragma_spec(source)
            # check if we need to install specified compiler version
            if pragma_spec and not pragma_spec.select(self.installed_versions):
                vyper_version = pragma_spec.select(self.available_versions)

                if vyper_version and vyper_version != self.package_version:
                    _install_vyper(vyper_version)

                else:
                    raise VyperInstallError("No available version to install.")

            elif pragma_spec:
                vyper_version = pragma_spec.select(self.installed_versions)

            elif not self.installed_versions:
                vyper_version = max(self.available_versions)
                _install_vyper(vyper_version)

            else:
                vyper_version = max(self.installed_versions)

            vyper_binary = shutil.which("vyper") if vyper_version is self.package_version else None
            try:
                result = vvm.compile_source(
                    source,
                    base_path=base_path,
                    vyper_version=vyper_version,
                    vyper_binary=vyper_binary,
                )["<stdin>"]
            except Exception as err:
                raise VyperCompileError(err) from err

            contract_path = (
                str(get_relative_path(path, base_path))
                if base_path and path.is_absolute()
                else str(path)
            )

            # NOTE: Vyper doesn't have internal contract type declarations, use filename
            result["contractName"] = Path(contract_path).stem
            result["sourceId"] = contract_path
            result["deploymentBytecode"] = {"bytecode": result["bytecode"]}
            result["runtimeBytecode"] = {"bytecode": result["bytecode_runtime"]}
            contract_types.append(ContractType.parse_obj(result))

        return contract_types
