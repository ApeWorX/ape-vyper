from pathlib import Path
from typing import List

import pytest  # type: ignore
from vvm.exceptions import VyperError  # type: ignore

from ape_vyper.exceptions import VyperCompileError, VyperInstallError

BASE_CONTRACTS_PATH = Path(__file__).parent / "contracts"


def contract_test_cases(passing: bool) -> List[str]:
    """
    Returns test-case names for outputting nicely with pytest.
    """
    suffix = "passing_contracts" if passing else "failing_contracts"
    return [p.name for p in (BASE_CONTRACTS_PATH / suffix).glob("*.vy")]


PASSING_CONTRACT_NAMES = contract_test_cases(True)
FAILING_CONTRACT_NAMES = contract_test_cases(False)
EXPECTED_FAIL_MESSAGES = {
    "contract_undeclared_variable": "'hello' has not been declared",
    "contract_unknown_pragma": "",
}


@pytest.mark.parametrize("contract_name", PASSING_CONTRACT_NAMES)
def test_pass(contract_name, compiler):
    path = BASE_CONTRACTS_PATH / "passing_contracts" / contract_name
    assert compiler.compile([path])


@pytest.mark.parametrize(
    "contract_name", [n for n in FAILING_CONTRACT_NAMES if n != "contract_unknown_pragma.vy"]
)
def test_failure_from_compile(contract_name, compiler):
    path = BASE_CONTRACTS_PATH / "failing_contracts" / contract_name
    with pytest.raises(VyperCompileError) as err:
        compiler.compile([path])

    assert isinstance(err.value.base_err, VyperError)
    assert EXPECTED_FAIL_MESSAGES[path.stem] in str(err.value)


def test_failure_from_install(compiler):
    path = BASE_CONTRACTS_PATH / "failing_contracts" / "contract_unknown_pragma.vy"
    with pytest.raises(VyperInstallError) as err:
        compiler.compile([path])

    assert str(err.value) == "No available version to install."
