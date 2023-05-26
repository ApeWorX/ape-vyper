# @version ^0.3.3

from interfaces import IFace as IFace


@external
@view
def read_contract(some_address: address) -> uint256:
    myContract: IFace = IFace(some_address)
    return myContract.read_stuff()
