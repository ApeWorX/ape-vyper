# @version 0.3.7

from vyper.interfaces import ERC20

bar1: public(uint256)
bar2: public(address)

event FooHappened:
    foo: indexed(uint256)

@external
def foo1() -> bool:
    return True

@external
def foo2(a: uint256, b: address) -> uint256:
    assert a != 0, "zero"
    self.bar1 = self.baz(a)
    log FooHappened(self.bar1)
    return self.bar1


@internal
def baz(a: uint256) -> uint256:
    return a + 123
