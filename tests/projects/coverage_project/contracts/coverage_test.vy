# @version 0.3.7

_number: public(uint256)
_immutable_number: public(immutable(uint256))

@external
def __init__(_number: uint256):
    _immutable_number = _number


@external
def foo_method(a: uint256 = 3, b: uint256 = 1) -> bool:
    assert a != 0  # dev: sub-zero
    self._number = a + b
    return True

@view
@external
def view_method() -> bool:
    return True

@external
def DEBUG_ignore_me() -> bool:
    return True
