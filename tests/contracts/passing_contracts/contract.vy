# @version 0.3.7

from vyper.interfaces import ERC20

interface ERC20Ext:
    def decimals() -> uint8: view

dynArray: public(DynArray[uint256, 1024])
token: public(immutable(address))

# NOTE: Keep constant as test for proving it doesn't fudge up PCMap.
MASK: constant(uint256) = 2**96 - 1


# NOTE: Keep event as test for proving it doesn't fudge up PCMap.
event Swap:
    account: indexed(address)
    receiver: indexed(address)
    asset_in: uint256
    asset_out: uint256
    amount_in: uint256
    amount_out: uint256


@external
def __init__(_token: address):
    """
    @notice Constructor
    @param _token Include docs to prove it doesn't fudge with PCMap.
    """
    token = _token


@external
def setNumber(num: uint256):
    # NOTE: This `and` statement `assert` purposely tests something
    #  we had an issue where this causes the PCMap calculation to get thrown off.
    assert num != 5 and num != 5556  # dev: 7 8 9

    # Show that PCMap can handle log statements.
    log Swap(msg.sender, msg.sender, 1, 2, 3, 4)

    # WARN: This part is really important.
    # We had a bug where doing this caused PC calculation to be off.
    ERC20Ext(token).decimals()


@external
def num_add(i: int128) -> int128:
    return (2**127-1) + i


@external
def neg_num_add(i: int128) -> int128:
    return i - (2**127-1)


@external
def div_zero(i: int128) -> int128:
    return 4 / i


@external
def mod_zero(i: int128) -> int128:
    return 4 % i


@external
def gimme(idx: uint256) -> uint256:
    return self.dynArray[idx]

