from pathlib import Path
from typing import List

import pytest
from semantic_version import Version
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


def test_compile_project(project):
    contracts = project.load_contracts()
    assert len(contracts) == 2
    assert contracts["contract"].source_id == "contract.vy"
    assert contracts["contract_no_pragma"].source_id == "contract_no_pragma.vy"


@pytest.mark.parametrize("contract_name", PASSING_CONTRACT_NAMES)
def test_compile_individual_contracts(contract_name, compiler):
    path = BASE_CONTRACTS_PATH / "passing_contracts" / contract_name
    assert compiler.compile([path])


@pytest.mark.parametrize(
    "contract_name", [n for n in FAILING_CONTRACT_NAMES if n != "contract_unknown_pragma.vy"]
)
def test_compile_failures(contract_name, compiler):
    path = BASE_CONTRACTS_PATH / "failing_contracts" / contract_name
    with pytest.raises(VyperCompileError) as err:
        compiler.compile([path])

    assert isinstance(err.value.base_err, VyperError)
    assert EXPECTED_FAIL_MESSAGES[path.stem] in str(err.value)


def test_install_failure(compiler):
    path = BASE_CONTRACTS_PATH / "failing_contracts" / "contract_unknown_pragma.vy"
    with pytest.raises(VyperInstallError) as err:
        compiler.compile([path])

    assert str(err.value) == "No available version to install."


def test_get_version_map(project, compiler):
    version_map = compiler.get_version_map([x for x in project.contracts_folder.iterdir()])
    assert len(version_map) == 2
    assert len(version_map[Version("0.2.8")]) == 1
    assert version_map[Version("0.2.8")].pop().name == "contract.vy"

    # Uses the latest when no pragma is specified
    assert len(version_map[Version("0.3.4")]) == 1
    assert version_map[Version("0.3.4")].pop().name == "contract_no_pragma.vy"


@pytest.mark.xfail(
    reason="Remove xfail when https://github.com/ApeWorX/ape/pull/871 is released", strict=False
)
def test_compiler_data_in_manifest(project):
    _ = project.contracts
    manifest = project.extract_manifest()
    assert len(manifest.compilers) == 2

    vyper_034 = [c for c in manifest.compilers if str(c.version) == "0.3.4"][0]
    vyper_028 = [c for c in manifest.compilers if str(c.version) == "0.2.8"][0]

    assert vyper_034.name == "vyper"
    assert vyper_028.name == "vyper"
    assert vyper_034.contractTypes == ["contract_no_pragma"]
    assert vyper_028.contractTypes == ["contract"]
