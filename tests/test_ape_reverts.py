import re

import pytest
from ape.pytest.contextmanagers import RevertsContextManager as reverts


@pytest.fixture(params=("021", "022", "023", "0215", "0216", "034"))
def older_reverts_contract(account, project, geth_provider, request):
    container = project.get_contract(f"sub_reverts_{request.param}")
    return container.deploy(sender=account)


@pytest.fixture(params=("037", "039", "0310rc3"))
def reverts_contract_instance(account, project, geth_provider, request):
    sub_reverts_container = project.get_contract(f"sub_reverts_{request.param}")
    sub_reverts = sub_reverts_container.deploy(sender=account)
    contract = project.get_contract(f"reverts_{request.param}")
    return contract.deploy(sub_reverts, sender=account)


def test_dev_revert(account, reverts_contract_instance, geth_provider):
    """
    Test matching a contract dev revert message with a supplied dev message.
    """
    with reverts(dev_message="dev: error"):
        reverts_contract_instance.revertStrings(2, sender=account)

    # Show a method further down in the contract also works.
    with reverts(dev_message="dev: error"):
        reverts_contract_instance.revertStrings2(2, sender=account)


def test_dev_revert_pattern(account, reverts_contract_instance, geth_provider):
    """
    Test matching a contract dev revert message with a supplied dev message pattern.
    """
    with reverts(dev_message=re.compile(r"dev: err\w+")):
        reverts_contract_instance.revertStrings(2, sender=account)

    with reverts(dev_message=re.compile(r"dev: err\w+")):
        reverts_contract_instance.revertStrings2(2, sender=account)


def test_dev_revert_from_sub_contract(account, reverts_contract_instance, geth_provider, project):
    """
    Test to ensure we can assert on dev messages from inner-contracts.
    """
    with reverts(dev_message="dev: sub-zero"):
        reverts_contract_instance.subRevertStrings(0, sender=account)


def test_dev_revert_deep_in_method(account, reverts_contract_instance, geth_provider):
    """
    Test to ensure we can assert on a dev message that is in the middle of a
    complicated function implementation.
    """
    with reverts(dev_message="dev: foobarbaz"):
        reverts_contract_instance.revertStrings(13, sender=account)

    with reverts(dev_message="dev: such modifiable, wow"):
        reverts_contract_instance.revertStrings(4, sender=account)

    with reverts(dev_message="dev: great job"):
        reverts_contract_instance.revertStrings(31337, sender=account)


def test_dev_revert_in_loop(account, reverts_contract_instance, geth_provider):
    with reverts(dev_message="dev: loop test"):
        reverts_contract_instance.revertStrings2(12, sender=account)


def test_dev_revert_fails(account, reverts_contract_instance, geth_provider):
    """
    Test that ``AssertionError`` is raised if the supplied dev message and the contract dev message
    do not match.
    """
    with pytest.raises(AssertionError):
        with reverts(dev_message="dev: foo"):
            reverts_contract_instance.revertStrings(2, sender=account)


def test_dev_revert_partial_fails(account, reverts_contract_instance, geth_provider):
    """
    Test that ``AssertionError`` is raised if the supplied dev message and the contract dev message
    do not match exactly.
    """
    with pytest.raises(AssertionError):
        with reverts(dev_message="dev: foo"):
            reverts_contract_instance.revertStrings(2, sender=account)


def test_dev_revert_pattern_fails(account, reverts_contract_instance, geth_provider):
    """
    Test that ``AssertionError`` is raised if the contract dev message does not match the supplied
    dev revert pattern.
    """
    with pytest.raises(AssertionError):
        with reverts(dev_message=re.compile(r"dev: [^ero]+")):
            reverts_contract_instance.revertStrings(2, sender=account)


def test_dev_revert_on_call(account, reverts_contract_instance, geth_provider):
    """
    Shows that dev strings are detectable even on pure / view methods.
    """
    with reverts(dev_message="dev: one"):
        reverts_contract_instance.revertStringsCall(1)


def test_both_message_and_dev_str(account, reverts_contract_instance, geth_provider):
    """
    Test matching a revert message with a supplied message as well as a contract dev revert message
    with a supplied dev message.
    """
    with reverts(expected_message="two", dev_message="dev: error"):
        reverts_contract_instance.revertStrings(2, sender=account)


def test_dev_message_older_versions(account, older_reverts_contract):
    with reverts(dev_message="dev: sub-zero"):
        older_reverts_contract.revertStrings(0, sender=account)
