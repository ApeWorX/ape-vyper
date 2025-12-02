import pytest


@pytest.fixture(scope="session")
def snekmate_mocks(project):
    snekmate_dep = project.dependencies["snekmate-mocks"]
    return snekmate_dep[list(snekmate_dep)[0]]


@pytest.fixture()
def token(snekmate_mocks, accounts):
    return snekmate_mocks.erc20_mock.deploy(
        "Mock",
        "MOCK",
        18,
        100 * 10**18,
        "Mock",
        "1",
        sender=accounts[-1],
    )
