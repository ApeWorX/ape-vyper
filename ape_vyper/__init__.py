from ape import plugins

from .compiler import FileType, VyperCompiler, VyperConfig


@plugins.register(plugins.Config)
def config_class():
    return VyperConfig


@plugins.register(plugins.CompilerPlugin)
def register_compiler():
    return tuple(e.value for e in FileType), VyperCompiler
