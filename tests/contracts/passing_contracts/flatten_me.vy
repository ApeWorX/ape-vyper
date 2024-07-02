# pragma version 0.3.10

from vyper.interfaces import ERC20

from .interfaces import IFace2 as IFaceTwo
from .interfaces import IFace as IFace
import exampledependency.Dependency as Dep


@external
@view
def read_contract(some_address: address) -> uint256:
    myContract: IFace = IFace(some_address)
    return myContract.read_stuff()


@external
@view
def read_another_contract(some_address: address) -> uint256:
    two: IFaceTwo = IFaceTwo(some_address)
    return two.read_stuff_3()


@external
@view
def read_from_dep(some_address: address) -> uint256:
    dep: Dep = Dep(some_address)
    return dep.read_stuff_2()


@external
def send_me(token_address: address, amount: uint256) -> bool:
    token: ERC20 = ERC20(token_address)
    return token.transferFrom(msg.sender, self, amount, default_return_value=True)
