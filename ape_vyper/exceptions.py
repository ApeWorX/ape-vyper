from ape.exceptions import CompilerError


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

    def __init__(self, err: Exception):
        self.base_err = err
        if hasattr(err, "stderr_data"):
            message = err.stderr_data
        else:
            message = str(err)

        self.message = message
        super().__init__(message)
