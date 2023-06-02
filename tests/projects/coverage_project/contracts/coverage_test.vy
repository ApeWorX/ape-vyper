# @version 0.3.7

@external
def foo_method(a: uint256) -> bool:
    assert a != 0  # dev: sub-zero
    return True
