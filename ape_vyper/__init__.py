from typing import Any

from ape import plugins


@plugins.register(plugins.Config)
def config_class():
    from .config import VyperConfig

    return VyperConfig


@plugins.register(plugins.CompilerPlugin)
def register_compiler():
    from ._utils import FileType
    from .compiler import VyperCompiler

    return tuple(e.value for e in FileType), VyperCompiler


def __getattr__(name: str) -> Any:
    if name == "FileType":
        from ._utils import FileType

        return FileType

    elif name == "VyperCompiler":
        from .compiler import VyperCompiler

        return VyperCompiler

    elif name == "VyperConfig":
        from .config import VyperConfig

        return VyperConfig

    else:
        raise AttributeError(name)


__all__ = [
    "FileType",
    "VyperCompiler",
    "VyperConfig",
]
