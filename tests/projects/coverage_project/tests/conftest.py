import pytest


@pytest.fixture
def START_NUM():
    return 1234567


@pytest.fixture
def account(accounts):
    return accounts[0]


@pytest.fixture
def contract(account, project, START_NUM):
    return account.deploy(project.coverage_test, START_NUM)
