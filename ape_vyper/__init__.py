from ape import plugins

from .compiler import VyperCompiler


@plugins.register(plugins.CompilerPlugin)
def register_compiler():
    return (".vy",), VyperCompiler
