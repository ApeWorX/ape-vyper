"""Utilities for dealing with Vyper AST"""

from ethpm_types import ABI, MethodABI
from ethpm_types.abi import ABIType
from vyper.ast import parse_to_ast  # type: ignore
from vyper.ast.nodes import FunctionDef, Module, Name, Subscript  # type: ignore

DEFAULT_VYPER_MUTABILITY = "nonpayable"
DECORATOR_MUTABILITY = {
    "pure",  # Function does not read contract state or environment variables
    "view",  # Function does not alter contract state
    "payable",  # Function is able to receive Ether and may alter state
    "nonpayable",  # Function may alter sate
}


def funcdef_decorators(funcdef: FunctionDef) -> list[str]:
    return [d.id for d in funcdef.get("decorator_list") or []]


def funcdef_inputs(funcdef: FunctionDef) -> list[ABIType]:
    """Get a FunctionDef's defined input args"""
    args = funcdef.get("args")
    # TODO: Does Vyper allow complex input types, like structs and arrays?
    return (
        [ABIType.model_validate({"name": arg.arg, "type": arg.annotation.id}) for arg in args.args]
        if args
        else []
    )


def funcdef_outputs(funcdef: FunctionDef) -> list[ABIType]:
    """Get a FunctionDef's outputs, or return values"""
    returns = funcdef.get("returns")

    if not returns:
        return []

    if isinstance(returns, Name):
        # TODO: Structs fall in here. I think they're supposed to be a tuple of types in the ABI.
        #       Need to dig into that more.
        return [ABIType.model_validate({"type": returns.id})]

    elif isinstance(returns, Subscript):
        # An array type
        length = returns.slice.value.value
        if array_type := getattr(returns.value, "id", None):
            # TOOD: Is this an accurate way to define a fixed length array for ABI?
            return [ABIType.model_validate({"type": f"{array_type}[{length}]"})]

    raise NotImplementedError(f"Unhandled return type {type(returns)}")


def funcdef_state_mutability(funcdef: FunctionDef) -> str:
    """Get a FunctionDef's declared state mutability"""
    for decorator in funcdef_decorators(funcdef):
        if decorator in DECORATOR_MUTABILITY:
            return decorator
    return DEFAULT_VYPER_MUTABILITY


def funcdef_is_external(funcdef: FunctionDef) -> bool:
    """Check if a FunctionDef is declared external"""
    for decorator in funcdef_decorators(funcdef):
        if decorator == "external":
            return True
    return False


def funcdef_to_abi(func: FunctionDef) -> ABI:
    """Return a MethodABI instance for a Vyper FunctionDef"""
    return MethodABI.model_validate(
        {
            "name": func.get("name"),
            "inputs": funcdef_inputs(func),
            "outputs": funcdef_outputs(func),
            "stateMutability": funcdef_state_mutability(func),
        }
    )


def module_to_abi(module: Module) -> list[ABI]:
    """
    Create a list of MethodABIs from a Vyper AST Module instance.
    """
    abi = []
    for child in module.get_children():
        if isinstance(child, FunctionDef):
            abi.append(funcdef_to_abi(child))
    return abi


def source_to_abi(source: str) -> list[ABI]:
    """
    Given Vyper source code, return a list of Ape ABI elements needed for an external interface.
    This currently does not include complex types or events.
    """
    module = parse_to_ast(source)
    return module_to_abi(module)
