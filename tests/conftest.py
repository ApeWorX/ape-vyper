import pytest  # type: ignore

from ape_vyper.compiler import VyperCompiler


@pytest.fixture
def compiler():
    return VyperCompiler()
