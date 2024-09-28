# Show we can import from the root of the project w/o needing relative imports
from tests.contracts.passing_contracts import zero_four_module as zero_four_module

@external
def callModuleFunctionFromSubdir(role: bytes32) -> bool:
    return zero_four_module.moduleMethod()
