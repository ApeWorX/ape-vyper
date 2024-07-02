# @version ^0.3.3

# Import a local interface.
from .interfaces import IFace as IFace

# Import from input JSON (ape-config.yaml).
import exampledependency.Dependency as Dep

from .interfaces import IFace2 as IFace2


@external
@view
def read_contract(some_address: address) -> uint256:
    myContract: IFace = IFace(some_address)
    return myContract.read_stuff()
