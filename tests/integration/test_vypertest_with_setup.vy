# NOTE: Tests can have state!
store: uint256


@external
def setUp():
    """
    @notice
        If a test has the method `setUp`, it will be executed **every time** before starting the test.
        This can be useful to set up state or other conditions in your tests.

    @dev
        Even though it is executed every time, each test runs in isolation from each other, and should
        not have an affect on future tests.
    """
    self.store += 1


@external
def test_setup_works():
    assert self.store == 1


@external
def test_setup_works_2nd_time():
    assert self.store == 1
