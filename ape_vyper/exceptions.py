from ape.exceptions import CompilerError


class VyperInstallError(CompilerError):
    """
    An error raised failing to install Vyper.
    """


class VyperCompileError(CompilerError):
    """
    A compiler-specific error in Vyper.
    """

    def __init__(self, err: Exception):
        self.base_err = err
        if hasattr(err, "stderr_data"):
            message = err.stderr_data  # type: ignore
        else:
            message = str(err)

        self.message = message
        super().__init__(message)
