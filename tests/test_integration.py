from pathlib import Path
from typing import List

import pytest
from semantic_version import Version  # type: ignore
from vvm.exceptions import VyperError  # type: ignore

from ape_vyper.exceptions import VyperCompileError, VyperInstallError

BASE_CONTRACTS_PATH = Path(__file__).parent / "contracts"

# Currently, this is the only version specified from a pragma spec
VERSION_FROM_PRAGMA = Version("0.2.8")


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
    with pytest.raises(VyperCompileError, match=EXPECTED_FAIL_MESSAGES[path.stem]) as err:
        compiler.compile([path])

    assert isinstance(err.value.base_err, VyperError)


def test_install_failure(compiler):
    path = BASE_CONTRACTS_PATH / "failing_contracts" / "contract_unknown_pragma.vy"
    with pytest.raises(VyperInstallError, match="No available version to install."):
        compiler.compile([path])


def test_get_version_map(project, compiler):
    version_map = compiler.get_version_map([x for x in project.contracts_folder.iterdir()])
    assert len(version_map) == 2
    assert len(version_map[VERSION_FROM_PRAGMA]) == 1
    assert version_map[VERSION_FROM_PRAGMA].pop().name == "contract.vy"

    # Uses the latest when no pragma is specified
    latest_version = [v for v in version_map if v != VERSION_FROM_PRAGMA][0]
    assert len(version_map[latest_version]) == 1
    assert version_map[latest_version].pop().name == "contract_no_pragma.vy"


def test_compiler_data_in_manifest(project):
    _ = project.contracts
    manifest = project.extract_manifest()
    assert len(manifest.compilers) == 2

    vyper_028 = [c for c in manifest.compilers if str(c.version) == str(VERSION_FROM_PRAGMA)][0]
    vyper_latest = [c for c in manifest.compilers if str(c.version) != str(VERSION_FROM_PRAGMA)][0]

    assert vyper_latest.name == "vyper"
    assert vyper_028.name == "vyper"
    assert vyper_latest.contractTypes == ["contract_no_pragma"]
    assert vyper_028.contractTypes == ["contract"]
