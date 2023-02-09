from ape import plugins

from .compiler import EXTENSIONS, VyperCompiler, VyperConfig


@plugins.register(plugins.Config)
def config_class():
    return VyperConfig


@plugins.register(plugins.CompilerPlugin)
def register_compiler():
    return EXTENSIONS, VyperCompiler
