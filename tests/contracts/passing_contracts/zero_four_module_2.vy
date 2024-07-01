# pragma version ~=0.4.0

# Showing importing interface from module.
interface Ballot:
    def delegated(addr: address) -> bool: view

@internal
def moduleMethod2() -> bool:
    return True
