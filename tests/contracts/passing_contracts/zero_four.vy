# pragma version ~=0.4.0

import interfaces.IFaceZeroFour as IFaceZeroFour
implements: IFaceZeroFour

from . import zero_four_module as zero_four_module

# `zero_four_module.vy` also imports this next line.
# We are testing that the flattener can handle that.
from . import zero_four_module as zero_four_module_2

@external
@view
def implementThisPlease(role: bytes32) -> bool:
    return True


@external
def callModuleFunction(role: bytes32) -> bool:
    return zero_four_module.moduleMethod()
