# @version 0.3.7

from vyper.interfaces import ERC20
import interfaces.IRegistry as IRegistry


bar1: public(uint256)
bar2: public(address)
registry: public(IRegistry)


event FooHappened:
    foo: indexed(uint256)


@external
def __init__(registry: IRegistry):
    self.registry = registry


@external
def foo1() -> bool:
    return True

# NOTE: Purposely making signature multi-line.
@external  # TEST COMMENT 0 def foo2():
def foo2(  # TEST COMMENT 1 def foo2():
    a: uint256,  # TEST COMMENT 2 def foo2():
    b: address # TEST COMMENT 3 def foo2():
) -> uint256:  # TEST COMMENT 4 def foo2():
    assert a != 0, "zero"  # TEST COMMENT 5 def foo2():
    self.registry.register(b)  # TEST COMMENT 6 def foo2():
    self.bar1 = self.baz(a)  # TEST COMMENT 7 def foo2():
    log FooHappened(self.bar1)  # TEST COMMENT 8 def foo2():
    return self.bar1  # TEST COMMENT 9 def foo2():


@internal
def baz(a: uint256) -> uint256:
    return a + 123
