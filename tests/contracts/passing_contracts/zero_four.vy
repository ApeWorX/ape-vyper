# pragma version ~=0.4.0

from .interfaces import IFaceZeroFour as IFaceZeroFour
implements: IFaceZeroFour

from . import zero_four_module as zero_four_module

from snekmate.auth import ownable

# Also show we can import from ethereum namespace.
# (new in Vyper 0.4).
from ethereum.ercs import IERC20

# `zero_four_module.vy` also imports this next line.
# We are testing that the flattener can handle that.
from . import zero_four_module_2 as zero_four_module_2

@external
@view
def implementThisPlease(role: bytes32) -> bool:
    return True


@external
def callModuleFunction(role: bytes32) -> bool:
    return zero_four_module.moduleMethod()


@external
def callModule2Function(role: bytes32) -> bool:
    return zero_four_module_2.moduleMethod2()
