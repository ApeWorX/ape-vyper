@external
def test_with_fuzzing(a: uint256):
    """
    @notice
        If you have args that do not match any existing fixture, and are not parametrized,
        then they will be considered "fuzzing strategy" arguments. This will turn your test
        into a "fuzz test", which modifies the test runner into calling it many times with
        arguments pulled at random according to a specific Hypothesis strategy.

    @dev
        All basic ABI types have a default strategy that will be used if no modifiers are provided.
        Provide `@custom:test:fuzz:<setting_name> value` to specify test settings

    @custom:test:fuzz:max_examples 200
    @custom:test:fuzz:deadline 2000
    """

    assert a != 29678634502528050652056023465820843, "Found a rare bug!"


# TODO: `.filter` modifiers to pull more relevant values
# TOOD: `.map` modifier?
