import os
import re
import subprocess
import time
from collections.abc import Iterable, Iterator
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

import vvm  # type: ignore
from ape.exceptions import ProjectError
from ape.logging import logger
from ape.managers import ProjectManager
from ape.utils import get_relative_path
from eth_utils import is_0x_prefixed
from ethpm_types import ASTNode, PCMap, SourceMapItem
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import Version
from semantic_version import NpmSpec  # type: ignore[import-untyped]
from semantic_version import Version as NpmVersion  # type: ignore[import-untyped]
from vvm.exceptions import UnknownOption, UnknownValue  # type: ignore

from ape_vyper.exceptions import RuntimeErrorType, VyperCompileError, VyperError, VyperInstallError

if TYPE_CHECKING:
    from ape.types.trace import SourceTraceback
    from ethpm_types.source import Function

Optimization = str | bool
EVM_VERSION_DEFAULT = {
    "0.2.15": "berlin",
    "0.2.16": "berlin",
    "0.3.0": "berlin",
    "0.3.1": "berlin",
    "0.3.2": "berlin",
    "0.3.3": "berlin",
    "0.3.4": "berlin",
    "0.3.6": "berlin",
    "0.3.7": "paris",
    "0.3.8": "shanghai",
    "0.3.9": "shanghai",
    "0.3.10": "shanghai",
    "0.4.0": "shanghai",
}
DEV_MSG_PATTERN = re.compile(r".*\s*#\s*(dev:.+)")
RETURN_OPCODES = ("RETURN", "REVERT", "STOP")
FUNCTION_DEF = "FunctionDef"
FUNCTION_AST_TYPES = (FUNCTION_DEF, "Name", "arguments")
EMPTY_REVERT_OFFSET = 18
NON_PAYABLE_STR = f"dev: {RuntimeErrorType.NONPAYABLE_CHECK.value}"

MAX_INSTALL_RETRIES = 5
INSTALL_RETRY_BACKOFF_FACTOR = 2  # seconds


class FileType(str, Enum):
    SOURCE = ".vy"
    INTERFACE = ".vyi"

    def __str__(self) -> str:
        return self.value


def install_vyper(version: "Version"):
    for attempt in range(MAX_INSTALL_RETRIES):
        try:
            vvm.install_vyper(version, show_progress=True)
            return  # If installation is successful, exit the loop
        except Exception as err:
            if "API rate limit exceeded" in str(err):
                if attempt < MAX_INSTALL_RETRIES - 1:  # Don't sleep after the last attempt
                    sleep_time = INSTALL_RETRY_BACKOFF_FACTOR * (2**attempt)
                    logger.warning(f"Rate limit exceeded. Retrying in {sleep_time} seconds...")
                    time.sleep(sleep_time)
                else:
                    raise VyperInstallError(
                        f"Unable to install Vyper version: '{version}'"
                        f"after {MAX_INSTALL_RETRIES} attempts due to "
                        f"API rate limit.\nReason: {err}"
                    ) from err
            else:
                raise VyperInstallError(
                    f"Unable to install Vyper version: '{version}'.\nReason: {err}"
                ) from err


VERSION_PRAGMA_PATTERN = re.compile(
    r"(?:\n|^)\s*#\s*(?P<style>@version|pragma\s+version)\s*(?P<version>[^\n]*)"
)
# Vyper 0.3.10 switched version pragma matching from NpmSpec to SpecifierSet.
VYPER_PEP440_PRAGMA_START_VERSION = Version("0.3.10")


def _as_pep440_spec(pragma_str: str) -> str:
    pragma_str = pragma_str.replace("^", "~=")
    if pragma_str and pragma_str[0].isnumeric():
        return f"=={pragma_str}"

    return pragma_str


def _as_npm_version(version: Version) -> NpmVersion:
    version_str = str(version)
    version_str = re.sub(r"(?<=\d)a(?=\d)", "-alpha.", version_str)
    version_str = re.sub(r"(?<=\d)b(?=\d)", "-beta.", version_str)
    version_str = re.sub(r"(?<=\d)rc(?=\d)", "-rc.", version_str)
    return NpmVersion(version_str)


class VyperVersionSpecifier:
    def __init__(self, pragma_str: str, style: str, source_path: Path | None = None):
        self.pragma_str = pragma_str
        self.style = style
        self.source_path = source_path
        self._npm_spec: NpmSpec | None = None
        self._pep440_spec: SpecifierSet | None = None

        try:
            self._npm_spec = NpmSpec(pragma_str)
        except ValueError:
            pass

        try:
            self._pep440_spec = SpecifierSet(_as_pep440_spec(pragma_str))
        except InvalidSpecifier:
            pass

        if not self._npm_spec and not self._pep440_spec:
            raise self._error()

        if self.is_modern_pragma and not self._pep440_spec:
            raise self._error()

    @property
    def is_modern_pragma(self) -> bool:
        return self.style.startswith("pragma")

    def match(self, version: Version) -> bool:
        if version >= VYPER_PEP440_PRAGMA_START_VERSION:
            return bool(self._pep440_spec and self._pep440_spec.contains(version, prereleases=True))

        return not self.is_modern_pragma and bool(
            self._npm_spec and self._npm_spec.match(_as_npm_version(version))
        )

    def contains(self, version: str | Version) -> bool:
        return self.match(version if isinstance(version, Version) else Version(version))

    def filter(self, versions: Iterable[Version]) -> Iterator[Version]:
        return (version for version in versions if self.match(version))

    def _error(self) -> VyperCompileError:
        source_label = f" in '{self.source_path}'" if self.source_path else ""
        return VyperCompileError(
            f"Invalid Vyper version pragma{source_label}: '{self.pragma_str}'."
        )

    def __str__(self) -> str:
        return self.pragma_str


def get_version_pragma_spec(source: str | Path) -> VyperVersionSpecifier | None:
    """
    Extracts version pragma information from Vyper source code.

    Args:
        source (str): Vyper source code

    Returns:
        ``VyperVersionSpecifier``, or None if no pragma is found.
    """
    source_str = source if isinstance(source, str) else source.read_text(encoding="utf8")
    source_path = source if isinstance(source, Path) else None
    if pragma_match := next(re.finditer(VERSION_PRAGMA_PATTERN, source_str), None):
        raw_pragma = pragma_match.group("version")
        pragma_str = " ".join(raw_pragma.split())
        if not pragma_str:
            source_label = f" in '{source_path}'" if source_path else ""
            raise VyperCompileError(f"Invalid Vyper version pragma{source_label}: missing version.")

        return VyperVersionSpecifier(
            pragma_str,
            style=" ".join(pragma_match.group("style").split()),
            source_path=source_path,
        )

    return None


def get_optimization_pragma(source: str | Path) -> str | None:
    """
    Extracts optimization pragma information from Vyper source code.

    Args:
        source (str | Path): Vyper source code

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


def get_evm_version_pragma(source: str | Path) -> str | None:
    """
    Extracts evm version pragma information from Vyper source code.

    Args:
        source (str | Path): Vyper source code

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
    config_override: dict | None = None,
) -> tuple[Path, ProjectManager] | None:
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

    def seek() -> Path | None:
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
        f"'{imported_project.path}'. "
        f"Contracts folder: {contracts_path}, "
        f"Existing file(s): {fs_str}"
    )
    return None


def safe_append(
    data: dict, version: "Version | SpecifierSet | VyperVersionSpecifier", paths: Path | set
):
    if isinstance(paths, Path):
        paths = {paths}
    if version in data:
        data[version] = data[version].union(paths)
    else:
        data[version] = paths


def is_revert_jump(op: str, value: int | None, revert_pc: int) -> bool:
    return op == "JUMPI" and value is not None and value == revert_pc


def has_empty_revert(opcodes: list[str]) -> bool:
    return (len(opcodes) > 12 and opcodes[-13] == "JUMPDEST" and opcodes[-9] == "REVERT") or (
        len(opcodes) > 4 and opcodes[-5] == "JUMPDEST" and opcodes[-1] == "REVERT"
    )


def get_pcmap(bytecode: dict) -> PCMap:
    # Find the non-payable value check.
    src_info = bytecode["sourceMapFull"] if "sourceMapFull" in bytecode else bytecode["sourceMap"]
    pc_data = {pc: {"location": ln} for pc, ln in src_info["pc_pos_map"].items()}
    if not pc_data:
        return PCMap.model_validate({})

    # Apply other errors.
    errors = src_info["error_map"]
    for err_pc, error_type in errors.items():
        use_loc = True
        if "safemul" in error_type or "safeadd" in error_type or "bounds check" in error_type:
            # NOTE: Bound check may also occur for underflow.
            error_str = RuntimeErrorType.INTEGER_OVERFLOW.value
        elif "safesub" in error_type or "clamp" in error_type:
            error_str = RuntimeErrorType.INTEGER_UNDERFLOW.value
        elif "safediv" in error_type or "clamp gt 0" in error_type:
            error_str = RuntimeErrorType.DIVISION_BY_ZERO.value
        elif "safemod" in error_type:
            error_str = RuntimeErrorType.MODULO_BY_ZERO.value
        elif "bounds check" in error_type:
            error_str = RuntimeErrorType.INDEX_OUT_OF_RANGE.value
        elif "user assert" in error_type.lower() or "user revert" in error_type.lower():
            # Mark user-asserts so the Ape can correctly find dev messages.
            error_str = RuntimeErrorType.USER_ASSERT.value
        elif "fallback function" in error_type:
            error_str = RuntimeErrorType.FALLBACK_NOT_DEFINED.value
            use_loc = False
        elif "bad calldatasize or callvalue" in error_type:
            # Only on >=0.3.10.
            # NOTE: We are no longer able to get Nonpayable checks errors since they
            # are now combined.
            error_str = RuntimeErrorType.INVALID_CALLDATA_OR_VALUE.value
        elif "nonpayable check" in error_type:
            error_str = RuntimeErrorType.NONPAYABLE_CHECK.value
        else:
            error_str = ""
            error_type_name = error_type.upper().replace(" ", "_")
            for _type in RuntimeErrorType:
                if _type.name == error_type_name:
                    error_str = _type.value
                    break

            error_str = error_str or error_type_name
            use_loc = False

        location = None
        if use_loc:
            # Add surrounding locations
            for pc in range(int(err_pc), -1, -1):
                if (
                    (data := pc_data.get(f"{pc}"))
                    and "dev" not in data
                    and (location := data.get("location"))
                ):
                    break

        if err_pc in pc_data:
            pc_data[err_pc]["dev"] = f"dev: {error_str}"
        else:
            pc_data[err_pc] = {"dev": f"dev: {error_str}", "location": location}

    return PCMap.model_validate(pc_data)


def get_legacy_pcmap(ast: ASTNode, src_map: list[SourceMapItem], opcodes: list[str]):
    """
    For Vyper versions <= 0.3.7, allows us to still get a PCMap.
    """

    pc = 0
    pc_map_list: list[tuple[int, dict[str, Any | None]]] = []
    last_value = None
    revert_pc = -1
    if has_empty_revert(opcodes):
        revert_pc = get_revert_pc(opcodes)

    processed_opcodes = []

    # There is only 1 non-payable check and it happens early in the bytecode.
    non_payable_check_found = False

    # There is at most 1 fallback error PC
    fallback_found = False

    while src_map and opcodes:
        src = src_map.pop(0)
        op = opcodes.pop(0)
        processed_opcodes.append(op)
        pc += 1

        # If immutable member load, ignore increasing pc by push size.
        if is_immutable_member_load(opcodes):
            last_value = int(opcodes.pop(0), 16)
            # Add the push number, e.g. PUSH1 adds `1`.
            num_pushed = int(op[4:])
            pc += num_pushed

        # Add content PC item.
        # Also check for compiler runtime error handling.
        # Runtime error locations are marked in the PCMap for further analysis.
        if src.start is not None and src.length is not None:
            stmt = ast.get_node(src)
            if stmt:
                # Add located item.
                line_nos = list(stmt.line_numbers)
                item: dict = {"location": line_nos}
                is_rev_jump = is_revert_jump(op, last_value, revert_pc)
                if op == "REVERT" or is_rev_jump:
                    dev = None
                    if stmt.ast_type in ("AugAssign", "BinOp"):
                        # SafeMath
                        for node in stmt.children:
                            dev = RuntimeErrorType.from_operator(node.ast_type)
                            if dev:
                                break

                    elif stmt.ast_type == "Subscript":
                        dev = RuntimeErrorType.INDEX_OUT_OF_RANGE

                    else:
                        # This is needed for finding the corresponding dev message.
                        dev = RuntimeErrorType.USER_ASSERT

                    if dev:
                        val = f"dev: {dev.value}"
                        if is_rev_jump and len(pc_map_list) >= 1:
                            pc_map_list[-1][1]["dev"] = val
                        else:
                            item["dev"] = val

                pc_map_list.append((pc, item))

        elif not fallback_found and _is_fallback_check(opcodes, op):
            # You can tell this is the Fallback jump because it is checking for the method ID.
            item = {"dev": f"dev: {RuntimeErrorType.FALLBACK_NOT_DEFINED.value}", "location": None}
            # PC is actually the one before but it easier to detect here.
            pc_map_list.append((pc - 1, item))
            fallback_found = True

        elif not non_payable_check_found and is_non_payable_check(opcodes, op, revert_pc):
            item = {"dev": NON_PAYABLE_STR, "location": None}
            pc_map_list.append((pc, item))
            non_payable_check_found = True

        elif op == "REVERT":
            # Source-less revert found, use latest item with a source.
            for item in [x[1] for x in pc_map_list[::-1] if x[1]["location"]]:
                if not item.get("dev"):
                    item["dev"] = f"dev: {RuntimeErrorType.USER_ASSERT.value}"
                    break

    pcmap_data = dict(pc_map_list)
    return PCMap.model_validate(pcmap_data)


def find_non_payable_check(src_map: list[SourceMapItem], opcodes: list[str]) -> int | None:
    pc = 0
    revert_pc = -1
    if has_empty_revert(opcodes):
        revert_pc = get_revert_pc(opcodes)

    while src_map and opcodes:
        op = opcodes.pop(0)
        pc += 1

        # If immutable member load, ignore increasing pc by push size.
        if is_immutable_member_load(opcodes):
            # Add the push number, e.g. PUSH1 adds `1`.
            pc += int(op[4:])

        if is_non_payable_check(opcodes, op, revert_pc):
            return pc

    return None


def is_non_payable_check(opcodes: list[str], op: str, revert_pc: int) -> bool:
    return (
        len(opcodes) >= 3
        and op == "CALLVALUE"
        and "PUSH" in opcodes[0]
        and is_0x_prefixed(opcodes[1])
        and is_revert_jump(opcodes[2], int(opcodes[1], 16), revert_pc)
    )


def get_revert_pc(opcodes: list[str]) -> int:
    """
    Starting in vyper 0.2.14, reverts without a reason string are optimized
    with a jump to the "end" of the bytecode.
    """
    return (
        len(opcodes)
        + sum(int(i[4:]) - 1 for i in opcodes if i.startswith("PUSH"))
        - EMPTY_REVERT_OFFSET
    )


def is_immutable_member_load(opcodes: list[str]):
    is_code_copy = len(opcodes) > 5 and opcodes[5] == "CODECOPY"
    return not is_code_copy and opcodes and is_0x_prefixed(opcodes[0])


def extend_return(
    function: "Function", traceback: "SourceTraceback", last_pc: int, source_path: Path
):
    return_ast_result = [x for x in function.ast.children if x.ast_type == "Return"]
    if not return_ast_result:
        return

    # Ensure return statement added.
    # Sometimes it is missing from the PCMap otherwise.
    return_ast = return_ast_result[-1]
    location = return_ast.line_numbers

    last_lineno = max(0, location[2] - 1)
    for frameset in traceback.root[::-1]:
        if frameset.end_lineno is not None:
            last_lineno = frameset.end_lineno
            break

    start = last_lineno + 1
    last_pcs = {last_pc + 1} if last_pc else set()
    if traceback.last:
        traceback.last.extend(location, pcs=last_pcs, ws_start=start)
    else:
        # Not sure if it ever gets here, but type-checks say it could.
        traceback.add_jump(location, function, 1, last_pcs, source_path=source_path)


def _is_fallback_check(opcodes: list[str], op: str) -> bool:
    return (
        "JUMP" in op
        and len(opcodes) >= 7
        and opcodes[0] == "JUMPDEST"
        and opcodes[6] == "SHR"
        and opcodes[5] == "0xE0"
    )


def compile_files(
    binary: Path,
    source_files: list[Path],
    project_path: Path,
    output_format: list[str] | None = None,
    additional_paths: list[Path] | None = None,
    **kwargs,
) -> dict[str, Any]:
    """
    Borrowed (and modified) from vvm.
    """

    command = [f"{binary}", *[str(f) for f in source_files]]
    if not output_format:
        # Mirrors Vyper's default.
        output_format = ["bytecode"]

    command.extend(("-f", ",".join(output_format)))

    paths = [project_path, *(additional_paths or [])]
    for path in paths:
        command.extend(("-p", f"{path}"))

    if kwargs:
        command.extend(_kwargs_to_cli_options(**kwargs))

    process = subprocess.run(command, capture_output=True)
    if process.returncode != 0:
        raise _handle_process_failure(process)

    outputs = process.stdout.decode("utf-8").splitlines()
    iter_length = len(output_format)
    return {
        f"{input_source}": dict(zip(output_format, outputs[i : i + iter_length], strict=False))
        for i, input_source in zip(range(0, len(outputs), iter_length), source_files, strict=False)
    }


def _kwargs_to_cli_options(**kwargs) -> list[str]:
    options = []
    for key, value in kwargs.items():
        if not value:
            continue

        key = f"-{key}" if len(key) == 1 else f"--{key.replace('_', '-')}"
        if value is True:
            # Is a flag.
            options.append(key)

        else:
            # Has a value.
            value = (
                ",".join([f"{v}" for v in value])
                if isinstance(value, (list, tuple))
                else f"{value}"
            )
            options.extend((key, value))

    return options


def _to_string(key: str, value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ",".join(_to_string(key, i) for i in value)

    return f"{value}"


def _handle_process_failure(process) -> Exception:
    bin_name = process.args[-1].split(os.path.sep)[-1].split("-")
    stderr = process.stderr.decode("utf-8")
    if stderr.startswith("unrecognised option"):
        # unrecognised option '<FLAG>'
        flag = stderr.split("'")[1]
        return UnknownOption(f"{bin_name} does not support the '{flag}' option'")

    if stderr.startswith("Invalid option"):
        # Invalid option to <FLAG>: <OPTION>
        flag, option = stderr.split(": ")
        flag = flag.split(" ")[-1]
        return UnknownValue(
            f"{bin_name} does not accept '{option}' as an option for the '{flag}' flag"
        )

    return VyperError.from_process(process)
