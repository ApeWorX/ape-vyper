# pragma version ~=0.4.0

# This source is also imported from `zero_four.py` to test
# multiple imports across sources during flattening.
from . import zero_four_module_2 as zero_four_module_2

@internal
def moduleMethod() -> bool:
    return True


@external
def callModule2FunctionFromAnotherSource(role: bytes32) -> bool:
    return zero_four_module_2.moduleMethod2()
