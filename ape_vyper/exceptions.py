from enum import Enum
from typing import Dict, Optional, Type, Union

from ape.exceptions import CompilerError, ContractLogicError
from vvm.exceptions import VyperError  # type: ignore


class VyperCompilerPluginError(CompilerError):
    """
    An error raised in the Vyper compiler.
    """


class VyperInstallError(VyperCompilerPluginError):
    """
    An error raised failing to install Vyper.
    """


class VyperCompileError(VyperCompilerPluginError):
    """
    A compiler-specific error in Vyper.
    """

    def __init__(self, err: Union[VyperError, str]):
        if isinstance(err, VyperError):
            self.base_err = err
            message = "\n\n".join(
                f"{e['sourceLocation']['file']}\n{e['type']}:{e['message']}" for e in err.error_dict
            )
        else:
            self.base_err = None
            message = str(err)

        super().__init__(message)


class RuntimeErrorType(Enum):
    NONPAYABLE_CHECK = "Cannot send ether to non-payable function"
    INDEX_OUT_OF_RANGE = "Index out of range"
    INTEGER_OVERFLOW = "Integer overflow"
    INTEGER_UNDERFLOW = "Integer underflow"
    DIVISION_BY_ZERO = "Division by zero"
    MODULO_BY_ZERO = "Modulo by zero"

    @classmethod
    def from_operator(cls, operator: str) -> Optional["RuntimeErrorType"]:
        if operator == "Add":
            return cls.INTEGER_OVERFLOW
        elif operator == "Sub":
            return cls.INTEGER_UNDERFLOW
        elif operator == "Div":
            return cls.DIVISION_BY_ZERO
        elif operator == "Mod":
            return cls.MODULO_BY_ZERO

        return None


class VyperRuntimeError(ContractLogicError):
    """
    An error raised when running EVM code, such as a index or math error.
    It is a type of ``ContractLogicError`` where the code came from the
    compiler and not directly from the source.
    """

    def __init__(self, error_type: RuntimeErrorType, **kwargs):
        super().__init__(error_type.value, **kwargs)


class NonPayableError(VyperRuntimeError):
    """
    Raised when sending ether to a non-payable function.
    """

    def __init__(self, **kwargs):
        super().__init__(RuntimeErrorType.NONPAYABLE_CHECK, **kwargs)


class IndexOutOfRangeError(VyperRuntimeError, IndexError):
    """
    Raised when accessing an array using an out-of-range index.
    """

    def __init__(self, **kwargs):
        super().__init__(RuntimeErrorType.INDEX_OUT_OF_RANGE, **kwargs)


class IntegerOverflowError(VyperRuntimeError):
    """
    Raised when addition results in an integer exceeding its max size.
    """

    def __init__(self, **kwargs):
        super().__init__(RuntimeErrorType.INTEGER_OVERFLOW, **kwargs)


class IntegerUnderflowError(VyperRuntimeError):
    """
    Raised when addition results in an integer exceeding its max size.
    """

    def __init__(self, **kwargs):
        super().__init__(RuntimeErrorType.INTEGER_UNDERFLOW, **kwargs)


class DivisionByZeroError(VyperRuntimeError, ZeroDivisionError):
    """
    Raised when dividing by zero.
    """

    def __init__(self, **kwargs):
        super().__init__(RuntimeErrorType.DIVISION_BY_ZERO, **kwargs)


class ModuloByZeroError(VyperRuntimeError, ZeroDivisionError):
    """
    Raised when modding by zero.
    """

    def __init__(self, **kwargs):
        super().__init__(RuntimeErrorType.MODULO_BY_ZERO, **kwargs)


RUNTIME_ERROR_MAP: Dict[RuntimeErrorType, Type[ContractLogicError]] = {
    RuntimeErrorType.NONPAYABLE_CHECK: NonPayableError,
    RuntimeErrorType.INDEX_OUT_OF_RANGE: IndexOutOfRangeError,
    RuntimeErrorType.INTEGER_OVERFLOW: IntegerOverflowError,
    RuntimeErrorType.INTEGER_UNDERFLOW: IntegerUnderflowError,
    RuntimeErrorType.DIVISION_BY_ZERO: DivisionByZeroError,
    RuntimeErrorType.MODULO_BY_ZERO: ModuloByZeroError,
}
