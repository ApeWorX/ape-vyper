import pytest


@pytest.fixture
def account(accounts):
    return accounts[0]


@pytest.fixture
def contract(account, project):
    return account.deploy(project.coverage_test)
