"""
Tools for working with ABI specs and Vyper interface source code
"""

from typing import TYPE_CHECKING, Any, Optional, Union

from ethpm_types import ABI, MethodABI

if TYPE_CHECKING:
    from ethpm_types.abi import ABIType


INDENT_SPACES = 4
INDENT = " " * INDENT_SPACES


def indent_line(line: str, level=1) -> str:
    """Indent a source line of code"""
    return f"{INDENT * level}{line}"


def generate_inputs(inputs: list["ABIType"]) -> str:
    """Generate the source code input args from ABI inputs"""
    return ", ".join(f"{i.name}: {i.type}" for i in inputs)


def generate_method(abi: MethodABI) -> str:
    """Generate Vyper interface method definition"""
    inputs = generate_inputs(abi.inputs)
    return_maybe = f" -> {abi.outputs[0].type}" if abi.outputs else ""
    return f"def {abi.name}({inputs}){return_maybe}: {abi.stateMutability}\n"


def abi_to_type(iface: dict[str, Any]) -> Optional[ABI]:
    """Convert a dict JSON-like interface to an ethpm-types ABI type"""
    if iface["type"] == "function":
        return MethodABI.model_validate(iface)
    return None


def generate_interface(abi: Union[list[dict[str, Any]], list[ABI]], iface_name: str) -> str:
    """
    Generate a Vyper interface source code from an ABI spec

    Args:
        abi (List[Union[Dict[str, Any], ABI]]): An ABI spec for a contract
        iface_name (str): The name of the interface

    Returns:
        ``str`` Vyper source code for the interface
    """
    source = f"interface {iface_name}:\n"

    for iface in abi:
        if isinstance(iface, dict):
            _iface = abi_to_type(iface)

            if _iface is None:
                continue

            # Re-assignment after None check because mypy
            iface = _iface

        if isinstance(iface, MethodABI):
            source += indent_line(generate_method(iface))

    return f"{source}\n"


def extract_meta(source_code: str) -> tuple[Optional[str], str]:
    """Extract version pragma, and return cleaned source"""
    version_pragma: Optional[str] = None
    cleaned_source_lines: list[str] = []

    """
    Pragma format changed a bit.

    >= 3.10: #pragma version ^0.3.0
    < 3.10: # @version ^0.3.0

    Both are valid until 0.4 where the latter may be deprecated
    """
    for line in source_code.splitlines():
        if line.startswith("#") and (
            ("pragma version" in line or "@version" in line) and version_pragma is None
        ):
            version_pragma = line
        else:
            cleaned_source_lines.append(line)

    return (version_pragma, "\n".join(cleaned_source_lines))


def extract_imports(source: str) -> tuple[str, str, str]:
    """
    Extract import lines from the source, return them and the source without imports

    Returns:
     Tuple[str, str, str]: (stdlib_import_lines, interface_import_lines, cleaned_source)
    """
    interface_import_lines = []
    stdlib_import_lines = []
    cleaned_source_lines = []

    for line in source.splitlines():
        if line.startswith("import ") or (line.startswith("from ") and " import " in line):
            if "vyper.interfaces" in line:
                stdlib_import_lines.append(line)
            else:
                interface_import_lines.append(line)
        else:
            cleaned_source_lines.append(line)

    return (
        "\n".join(stdlib_import_lines),
        "\n".join(interface_import_lines),
        "\n".join(cleaned_source_lines),
    )


def extract_import_aliases(source: str) -> dict[str, str]:
    """
    Extract import aliases from import lines

    Returns:
        Dict[str, str]: {import: alias}
    """
    aliases = {}
    for line in source.splitlines():
        if (
            line.startswith("import ") or (line.startswith("from ") and " import " in line)
        ) and " as " in line:
            subject_parts = line.split("import ")[1]
            alias_parts = subject_parts.split(" as ")
            iface_path_name = alias_parts[0].split(".")[-1]  # Remove path parts from import
            aliases[iface_path_name] = alias_parts[1]
    return aliases
