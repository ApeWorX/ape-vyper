# TODO: Support stateful tests
# see: https://hypothesis.readthedocs.io/en/latest/stateful.html

secret: public(uint256)


# Define a "Bundle", which is an offchain-managed collection of values
# that Hypothesis will pull at random in associated tests.
struct ABundle:
    a: uint256
# NOTE: Docs mention that Bundles could very well be overlap with internal storage


@external
def setUp():
    """
    @notice
        Set up any initial state for the test case.

    @dev
        Runs on every stateful test case start.
    """
    self.secret = 703895692105206524502680346056234


@external
def initialize_bundleA() -> DynArray[ABundle, 10]:
    """
    @notice
        Add some initial values to a bundle.

    @dev
        Same as @initializes in Hypothesis, allows to set up initial state of bundles.
        The return struct type's name is what is used to look up the bundle to inject it into.
        A single return or list return both are supported for convienence.
    """
    return [ABundle(a=1), ABundle(a=2)]


# TODO: how would parametrizing work? reject it?
@external
def rule_add(a: uint256) -> ABundle:
    """
    @notice
        A rule is an action that MAY be called by the stateful runner at each step in the test.
        Rules are picked at random, and follow the same rules as normal tests with regard to args.

    @dev
        If you wish to avoid calling it except under a particular scenario, add a precondition.
        Rules can also return Bundles.

    @custom:test:stateful:precondition self.secret() + a < 2**256
    """
    # NOTE: Due to precondition, will **never** fail
    self.secret += a

    return ABundle(a=a)


@external
def rule_subtract(bs: DynArray[ABundle, 10]):
    """
    @notice
        If a failure occurs when executing a rule, that will automatically raise a test failure.
        This may indicate a legitimate bug in what you are testing, or a design flaw in your test.

    @dev
        Each argument that has a type of `*Bundle` "consumes" a value from the associated bundle.
        You can specify multiple args this way, or use lists (list size is chosen at random).
    """
    # NOTE: This will likely fail after a few calls
    for b: ABundle in bs:
        self.secret -= b.a



@external
def invariant_dont_stop_believin():
    """
    @notice
        An invariant is called after every rule invocation, to check consistency of internal state.
        If it fails, it will automatically raise a test failure, likely indicating a legitimate bug.

    @dev
        Invariants are called with Multicall, when there are many at a time.
        An invariant MUST NOT have any args.
        If you wish to avoid calling it except under a particular scenario, add a precondition.

    @custom:test:stateful:precondition self.secret() > 10000
    """
    assert self.secret != 2378945823475283674509246524589
