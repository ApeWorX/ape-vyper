from pathlib import Path

import pytest

CONTRACT = """
# @version 0.3.7

@external
def foo_method(a: uint256) -> bool:
    assert a != 0  # dev: sub-zero
    return True
""".lstrip()
CONFTEST = """
import pytest

@pytest.fixture
def account(accounts):
    return accounts[0]

@pytest.fixture
def contract(account, project):
    return account.deploy(project.Contract)
"""
TESTS = """
def test_method(account, contract):
    contract.foo_method(123, sender=account)
""".lstrip()
EXPECTED_COVERAGE_REPORT = """
=============================== Coverage Profile ===============================
           Contract Coverage

  Name          Stmts   Miss   Cover
 ─────────────────────────────────────
  Contract.vy   3       0      100.0%
""".lstrip()


@pytest.fixture
def runner(pytester, config):
    here = Path.cwd()
    with config.using_project(here) as project:
        # Create contract(s).
        project.contracts_folder.mkdir(exist_ok=True, parents=True)
        contract = project.contracts_folder / "Contract.vy"

        # Create tests.
        contract.write_text(CONTRACT)
        pytester.makeconftest(CONFTEST)
        pytester.makepyfile(test_contract=TESTS)

        yield pytester


def test_coverage(runner, geth_provider):
    flags = ("--coverage", "--network", "ethereum:local:geth")
    result = runner.runpytest(*flags)
    result.assert_outcomes(passed=1)
    expected = EXPECTED_COVERAGE_REPORT.split("\n")
    for ex in expected:
        found = False
        for line in result.outlines:
            if line.startswith(ex):
                found = True
                break

        assert found, f"Unable to find expected line: {ex}"
