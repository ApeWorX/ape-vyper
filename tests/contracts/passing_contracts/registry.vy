# @version 0.3.7

addr: public(address)

@external
def register(addr: address):
    assert addr != self.addr
    self.addr = addr
