from ape import plugins

from ._utils import Extension
from .compiler import VyperCompiler, VyperConfig


@plugins.register(plugins.Config)
def config_class():
    return VyperConfig


@plugins.register(plugins.CompilerPlugin)
def register_compiler():
    return tuple([e.value for e in Extension]), VyperCompiler
