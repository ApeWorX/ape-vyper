import re
from collections.abc import Iterable
from enum import Enum
from pathlib import Path
from typing import Optional, Union

import vvm  # type: ignore
from ape.exceptions import ProjectError
from ape.logging import logger
from ape.managers import ProjectManager
from ape.utils import get_relative_path
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import Version

from ape_vyper.exceptions import VyperInstallError

Optimization = Union[str, bool]


class FileType(str, Enum):
    SOURCE = ".vy"
    INTERFACE = ".vyi"

    def __str__(self) -> str:
        return self.value


def install_vyper(version: Version):
    try:
        vvm.install_vyper(version, show_progress=True)
    except Exception as err:
        raise VyperInstallError(
            f"Unable to install Vyper version: '{version}'.\nReason: {err}"
        ) from err


def get_version_pragma_spec(source: Union[str, Path]) -> Optional[SpecifierSet]:
    """
    Extracts version pragma information from Vyper source code.

    Args:
        source (str): Vyper source code

    Returns:
        ``packaging.specifiers.SpecifierSet``, or None if no valid pragma is found.
    """
    _version_pragma_patterns: tuple[str, str] = (
        r"(?:\n|^)\s*#\s*@version\s*([^\n]*)",
        r"(?:\n|^)\s*#\s*pragma\s+version\s*([^\n]*)",
    )

    source_str = source if isinstance(source, str) else source.read_text(encoding="utf8")
    for pattern in _version_pragma_patterns:
        for match in re.finditer(pattern, source_str):
            raw_pragma = match.groups()[0]
            pragma_str = " ".join(raw_pragma.split()).replace("^", "~=")
            if pragma_str and pragma_str[0].isnumeric():
                pragma_str = f"=={pragma_str}"

            try:
                return SpecifierSet(pragma_str)
            except InvalidSpecifier:
                logger.warning(f"Invalid pragma spec: '{raw_pragma}'. Trying latest.")
                return None
    return None


def get_optimization_pragma(source: Union[str, Path]) -> Optional[str]:
    """
    Extracts optimization pragma information from Vyper source code.

    Args:
        source (Union[str, Path]): Vyper source code

    Returns:
        ``str``, or None if no valid pragma is found.
    """
    if isinstance(source, str):
        source_str = source
    elif not source.is_file():
        return None
    else:
        source_str = source.read_text(encoding="utf8")

    if pragma_match := next(
        re.finditer(r"(?:\n|^)\s*#pragma\s+optimize\s+([^\n]*)", source_str), None
    ):
        return pragma_match.groups()[0]

    return None


def get_evm_version_pragma(source: Union[str, Path]) -> Optional[str]:
    """
    Extracts evm version pragma information from Vyper source code.

    Args:
        source (Union[str, Path]): Vyper source code

    Returns:
        ``str``, or None if no valid pragma is found.
    """
    if isinstance(source, str):
        source_str = source
    elif not source.is_file():
        return None
    else:
        source_str = source.read_text(encoding="utf8")

    if pragma_match := next(
        re.finditer(r"(?:\n|^)\s*#pragma\s+evm-version\s+([^\n]*)", source_str), None
    ):
        return pragma_match.groups()[0]

    return None


def get_optimization_pragma_map(
    contract_filepaths: Iterable[Path],
    base_path: Path,
    default: Optimization,
) -> dict[str, Optimization]:
    pragma_map: dict[str, Optimization] = {}

    for path in contract_filepaths:
        res = get_optimization_pragma(path)
        pragma = default if res is None else res
        source_id = str(get_relative_path(path.absolute(), base_path.absolute()))
        pragma_map[source_id] = pragma

    return pragma_map


def get_evm_version_pragma_map(
    contract_filepaths: Iterable[Path], base_path: Path
) -> dict[str, str]:
    pragmas: dict[str, str] = {}
    for path in contract_filepaths:
        pragma = get_evm_version_pragma(path)
        if not pragma:
            continue

        source_id = str(get_relative_path(path.absolute(), base_path.absolute()))
        pragmas[source_id] = pragma

    return pragmas


def lookup_source_from_site_packages(
    dependency_name: str,
    filestem: str,
    config_override: Optional[dict] = None,
) -> Optional[tuple[Path, ProjectManager]]:
    # Attempt looking up dependency from site-packages.
    config_override = config_override or {}
    if "contracts_folder" not in config_override:
        # Default to looking through the whole release for
        # contracts. Most often, Python-based dependencies publish
        # only their contracts this way, and we are only looking
        # for sources so accurate project configuration is not required.
        config_override["contracts_folder"] = "."

    try:
        imported_project = ProjectManager.from_python_library(
            dependency_name,
            config_override=config_override,
        )
    except ProjectError:
        # Still attempt to let Vyper handle this during compilation.
        return None

    extensions = [*[f"{t}" for t in FileType], ".json"]

    def seek() -> Optional[Path]:
        for ext in extensions:
            try_source_id = f"{filestem}{ext}"
            if source_path := imported_project.sources.lookup(try_source_id):
                return source_path

        return None

    if res := seek():
        return res, imported_project

    # Still not found. Try again without contracts_folder set.
    # This will attempt to use Ape's contracts_folder detection system.
    # However, I am not sure this situation occurs, as Vyper-python
    # based dependencies are new at the time of writing this.
    new_override = config_override or {}
    if "contracts_folder" in new_override:
        del new_override["contracts_folder"]

    imported_project.reconfigure(**new_override)
    if res := seek():
        return res, imported_project

    # Still not found. Log a very helpful message.
    existing_filestems = [f.stem for f in imported_project.path.iterdir()]
    fs_str = ", ".join(existing_filestems)
    contracts_folder = imported_project.contracts_folder
    path = imported_project.path

    # This will log the calculated / user-set contracts_folder.
    contracts_path = f"{get_relative_path(contracts_folder, path)}"

    logger.error(
        f"Source for stem '{filestem}' not found in "
        f"'{imported_project.path}'."
        f"Contracts folder: {contracts_path}, "
        f"Existing file(s): {fs_str}"
    )
    return None
