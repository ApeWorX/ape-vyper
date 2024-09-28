# @version ^0.3.3

# Import a local interface.
implements: IFaceNested
from .interfaces import IFace as IFace

# Import from input JSON (ape-config.yaml).
import exampledependency.Dependency as Dep

from .interfaces import IFace2 as IFace2

# Also use IFaceNested to show we can use nested interfaces.
from tests.contracts.passing_contracts.interfaces.nested import IFaceNested as IFaceNested


@external
@view
def read_contract(some_address: address) -> uint256:
    myContract: IFace = IFace(some_address)
    return myContract.read_stuff()

@view
@external
def read_stuff_nested() -> uint256:
    return 1
