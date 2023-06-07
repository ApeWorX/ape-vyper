# @version 0.3.7

@external
def foo_method(a: uint256 = 1, b: uint256 = 2) -> bool:
    assert a != 0  # dev: sub-zero
    assert a != b
    return True

@external
def DEBUG_exclude_me() -> bool:
    return True
