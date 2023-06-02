# @version 0.3.7

event NotPayment:
    sender: indexed(address)

@external
def __default__():
    log NotPayment(msg.sender)
