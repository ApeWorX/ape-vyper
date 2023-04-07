from enum import Enum
from typing import Optional

from ape.exceptions import CompilerError
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

    def __init__(self, err: VyperError):
        self.base_err = err  # For debugging purposes.
        message = "\n\n".join(
            f"{e['sourceLocation']['file']}\n{e['type']}:{e['message']}" for e in err.error_dict
        )
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
