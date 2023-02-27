# @version 0.3.7

from vyper.interfaces import ERC20

@external
def setNumber(num: uint256):
    assert num != 5  # dev: 7 8 9
