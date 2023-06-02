import ape

from ape_vyper.exceptions import FallbackNotDefinedError, NonPayableError


def test_happy_path(contract, account):
    """
    Covers some implicit statements as well two source statements.
    """
    receipt = contract.foo_method(5, sender=account)
    assert receipt.return_value is True


def test_sad_path(contract, account):
    """
    Covers some implicit statements as well as one source statement.
    """
    with ape.reverts(dev_message="dev: sub-zero"):
        contract.foo_method(0, sender=account)


def test_non_payable(contract, account):
    """
    Coverage (at least) the implicit statement for non-payable check.
    """
    with ape.reverts(NonPayableError):
        contract.foo_method(5, sender=account, value=1)


def test_no_default_method(contract, account):
    """
    Covers the implicit check for fallback not defined.
    """
    with ape.reverts(FallbackNotDefinedError):
        contract(sender=account)
