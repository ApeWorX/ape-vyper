# pragma version ~=0.4.0
from snekmate.auth import ownable
from snekmate.tokens import erc20

initializes: ownable
initializes: erc20[ownable := ownable]

@deploy
def __init__(_name: String[25]):
    ownable.__init__()
    erc20.__init__(_name, "ERC20", 18, "name", "name2")
