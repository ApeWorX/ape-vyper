# @version 0.3.7

addr: public(address)

@external
def register(addr: address):
    self.addr = addr


@external
def register_f(addr: address):
    assert self.addr != addr, "doubling."
    self.addr = addr
