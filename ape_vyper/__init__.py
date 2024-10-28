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
