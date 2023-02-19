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
