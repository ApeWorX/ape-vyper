# pragma version ~=0.4.3
from ethereum.ercs import IERC20

# NOTE: Compatible w/ Foundry stdlib's VM(0x7109709ECfa91a80626fF3989D68f67F5b1DD12D)
#from ape.test import VM


@external
def test_it_works():
    """
    @notice
        Contract tests are tests written in a supported language that are executed with Ape.

    @dev
        Contract test files MUST start with `test_` and use one of the supported plugin extensions.
        Ape will search for cases by compiling the file and finding all ABI methods named `test_*`.
        All contract test cases MUST be `external` visibility (`public` allowed in Solidity).
    """
    assert 1 + 1 == 2, "We can do tests in vyper!"


@external
def test_using_fixtures(accounts: DynArray[address, 10], executor: address):
    """
    @notice
        Test cases can use args to access Python fixtures from your Ape test suite.
        Ape looks up the fixture by arg name and then provides that to call the method.

    @dev
        The fixtures MUST be valid ABI types, or convertible using Ape's conversion system.

        Valid Ape types include:
        - `AccountAPI` types (converts to `address`)
        - `ContractInstance` types (converts to `address` or interface types)
        - strings that Ape's conversion system supports
          e.g. `"vitalik.eth"`, `"WETH"`, `"500 USDC"`, etc.
    """
    # NOTE: `accounts` is actually an Ape fixture!
    for a: address in accounts:
        assert a.balance >= 10 ** 18

    # NOTE: the `executor` fixture is actually the caller of the test
    assert executor == msg.sender
    # NOTE: the `executor` fixture is in the `accounts` fixture
    assert executor in accounts


@external
def test_it_raises():
    """
    @notice
        We can check that the result of a test case creates a given exception.
        The test will be executed with Ape's `reverts` context manager,
        and the test will fail **only** if the test does NOT raise the appropiate error.

    @dev
        The exception should either be a string literal (like here), hex bytesvalue,
        or it should eval to a custom error type e.g. `my_contract.CustomError()`.
        If a string or hex literal is given, it will compare it to the error message.
        If no reason is given, it will simply check that a revert happened.

    @custom:test:check:reverts "It works!"
    """
    assert False, "It works!"


@external
def test_with_cheatcode(token: IERC20):  #, vm: VM):
    """
    @notice
        We can use some of pytest's markers on our tests.
        For example, the following mimics `@pytest.mark.xfail(reason=...)`

    @dev
        Currently, only pre-defined markers supported by Ape are allowed (no custom markers)

    @custom:mark:xfail We don't support the vm fixture yet, so this will fail
    """
    assert staticcall token.balanceOf(self) == 0
    # NOTE: Perform next call as `executor` (who deployed `token`)
    #extcall vm.prank(executor)
    extcall token.transfer(self, 1000)
    #extcall vm.stopPrank()
    assert staticcall token.balanceOf(self) == 1000


@external
def test_parametrizing(i: uint256):
    """
    @notice
        We can execute tests over a range of different scenarios.
        This test will actually be executed 3 different times,
        each with a different value (1, 2, 3).

    @dev
        Each scenario must be in a yaml-like list.
        This is similar to Foundry's "table tests".

    @custom:test:mark:parametrize i
    - 1
    - 2
    - 3
    """
    assert i > 0


@external
def test_parametrizing_multiple_args(a: address, b: uint256):
    """
    @notice
        To execute a test with multiple varying arguments, add them as comma-separated names.
        Add their scenarios as a tuple of their values.

    @dev
        **Important**: Each scenario MUST be wrapped as a tuple, and may contain spaces.

    @custom:test:mark:parametrize a,b
    - (0x1, 1)
    - (0x2, 2)
    - (0x3, 3)
    """
    assert convert(a, uint256) == b


@external
def test_emits(token: IERC20):
    """
    @notice
        We can check the result of a test has an exact set of logs in the receipt after executing the test.

    @dev
        Similar to Ape, we can omit arguments in the mock log objects and it will match any value.

    @custom:test:check:emits
    - token.Approval(owner=self, spender=msg.sender, value=100_000)
    - token.Approval(spender=msg.sender, value=10_000)
    - token.Approval(owner=self, spender=msg.sender)
    - token.Approval(owner=self, value=100)
    - token.Approval(value=10)
    - token.Approval()
    """
    extcall token.approve(msg.sender, 100_000)
    extcall token.approve(msg.sender, 10_000)
    extcall token.approve(msg.sender, 1_000)
    extcall token.approve(msg.sender, 100)
    extcall token.approve(msg.sender, 10)
    extcall token.approve(msg.sender, 1)
