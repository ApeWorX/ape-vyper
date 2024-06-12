# pragma version ~=0.4.0rc6

import interfaces.IFaceZeroFour as IFaceZeroFour
implements: IFaceZeroFour

from . import zero_four_module as zero_four_module

@external
@view
def implementThisPlease(role: bytes32) -> bool:
    return True


@external
def callModuleFunction(role: bytes32) -> bool:
    return zero_four_module.moduleMethod()
