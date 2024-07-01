# pragma version ~=0.4.0

from . import zero_four_module as zero_four_module_2

# Showing importing interface from module.
interface Ballot:
    def delegated(addr: address) -> bool: view

@internal
def moduleMethod2() -> bool:
    return True
