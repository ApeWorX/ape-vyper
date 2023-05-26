# @version 0.3.8

from vyper.interfaces import ERC20

interface ERC20Ext:
    def decimals() -> uint8: view

# Public storage (PCMap testing)
dynArray: public(DynArray[uint256, 1024])
token: public(address)
start_token: public(immutable(address))

# Constants (PCMap testing)
MASK: constant(uint256) = 2**96 - 1
FIVE: constant(uint256) = 5
POS_FIVE: constant(int128) = 5
FIVES: constant(uint256) = 555

# Internal storage (PCMap testing)
array_test: uint256[FIVE]


# NOTE: Keep event as test for proving it doesn't fudge up PCMap.
event Swap:
    account: indexed(address)
    receiver: indexed(address)
    asset_in: uint256
    asset_out: uint256
    amount_in: uint256
    amount_out: uint256


# Testing empty events in how it affects PCMap generation.
event Hollow: pass


@external
def __init__(_token: address):
    """
    @notice Constructor
    @param _token Include docs to prove it doesn't fudge with PCMap.
    """
    self.token = _token
    start_token = _token

    # Part of testing how internal arrays behave in PCMap locations.
    for asset in range(FIVE):
        self.array_test[asset] = asset * 7


# NOTE: `@nonreentrant` just to test that it doesn't affect PCMap generation.
@external
@nonreentrant('lock')
def setNumber(num: uint256) -> uint256:
    # NOTE: This `and` statement `assert` purposely tests something
    #  we had an issue where this causes the PCMap calculation to get thrown off.
    assert num != FIVE and num != FIVES  # dev: 7 8 9

    # Show that PCMap can handle log statements.
    log Swap(msg.sender, msg.sender, 1, 2, 3, 4)

    # Test that we can access state variables and PCMap still works.
    ERC20Ext(self.token).decimals()

    # WARN: This part is really important.
    # Specifically, the call to the immutable member start_token tests something.
    # This is because immutable variables are code offsets and not storage slots.
    ERC20Ext(start_token).decimals()

    res: uint256 = self.helper(num) * self.array_test[1]
    log Hollow()
    return res


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


@view
@external
def a_view_method(a: address) -> (uint256, uint256, uint256):
    assert a != msg.sender  # dev: msg sender check
    assert a != start_token  # dev: immut. token check
    assert a != self.token  # dev: mut. token check
    return (FIVE, FIVES, MASK)


@pure
@internal
def helper(a: uint256) -> uint256:
    assert a + 1 != FIVES or a - 1 != FIVES
    res: uint256 = unsafe_mul(a & MASK, FIVE) | FIVE | shift(a, POS_FIVE)
    if res * FIVE < FIVES:
        return res

    # NOTE: Empty raise statement in Vyper are different than Python.
    # This should be the same as an assert with no message.
    # This is intentionally here for testing PCMap generation.
    raise  # dev: empty raise stmts
