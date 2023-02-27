from pathlib import Path
from typing import List

import pytest
from semantic_version import Version  # type: ignore
from vvm import compile_source  # type: ignore
from vvm.exceptions import VyperError  # type: ignore

from ape_vyper.exceptions import VyperCompileError, VyperInstallError

BASE_CONTRACTS_PATH = Path(__file__).parent / "contracts"
PASSING_BASE = BASE_CONTRACTS_PATH / "passing_contracts"
FAILING_BASE = BASE_CONTRACTS_PATH / "failing_contracts"

# Currently, this is the only version specified from a pragma spec
OLDER_VERSION_FROM_PRAGMA = Version("0.2.8")
VERSION_FROM_PRAGMA = Version("0.3.7")


@pytest.fixture
def dev_revert_source():
    return PASSING_BASE / "contract_with_dev_messages.vy"


def contract_test_cases(passing: bool) -> List[str]:
    """
    Returns test-case names for outputting nicely with pytest.
    """
    suffix = "passing_contracts" if passing else "failing_contracts"
    return [p.name for p in (BASE_CONTRACTS_PATH / suffix).glob("*.vy") if p.is_file()]


PASSING_CONTRACT_NAMES = contract_test_cases(True)
FAILING_CONTRACT_NAMES = contract_test_cases(False)
EXPECTED_FAIL_MESSAGES = {
    "contract_undeclared_variable": "'hello' has not been declared",
    "contract_unknown_pragma": "",
}


def test_compile_project(project):
    contracts = project.load_contracts()
    assert len(contracts) == len([p.name for p in PASSING_BASE.glob("*.vy") if p.is_file()])
    assert contracts["contract"].source_id == "contract.vy"
    assert contracts["contract_no_pragma"].source_id == "contract_no_pragma.vy"
    assert contracts["older_version"].source_id == "older_version.vy"


@pytest.mark.parametrize("contract_name", PASSING_CONTRACT_NAMES)
def test_compile_individual_contracts(contract_name, compiler):
    path = PASSING_BASE / contract_name
    assert compiler.compile([path])


@pytest.mark.parametrize(
    "contract_name", [n for n in FAILING_CONTRACT_NAMES if n != "contract_unknown_pragma.vy"]
)
def test_compile_failures(contract_name, compiler):
    path = FAILING_BASE / contract_name
    with pytest.raises(VyperCompileError, match=EXPECTED_FAIL_MESSAGES[path.stem]) as err:
        compiler.compile([path], base_path=FAILING_BASE)

    assert isinstance(err.value.base_err, VyperError)


def test_install_failure(compiler):
    path = FAILING_BASE / "contract_unknown_pragma.vy"
    with pytest.raises(VyperInstallError, match="No available version to install."):
        compiler.compile([path])


def test_get_version_map(project, compiler):
    vyper_files = [
        x for x in project.contracts_folder.iterdir() if x.is_file() and x.suffix == ".vy"
    ]
    actual = compiler.get_version_map(vyper_files)
    expected_versions = (OLDER_VERSION_FROM_PRAGMA, VERSION_FROM_PRAGMA)

    for version, sources in actual.items():
        if version in expected_versions:
            continue

        sources = ", ".join([p.name for p in actual[version]])
        fail_message = f"Unexpected version '{version}' with sources: {sources}"
        pytest.fail(fail_message)

    assert len(actual[OLDER_VERSION_FROM_PRAGMA]) == 1
    assert len(actual[VERSION_FROM_PRAGMA]) == 5
    assert actual[OLDER_VERSION_FROM_PRAGMA] == {project.contracts_folder / "older_version.vy"}
    assert actual[VERSION_FROM_PRAGMA] == {
        project.contracts_folder / "contract.vy",
        project.contracts_folder / "contract_no_pragma.vy",
        project.contracts_folder / "contract_with_dev_messages.vy",
        project.contracts_folder / "use_iface.vy",
        project.contracts_folder / "use_iface2.vy",
    }


def test_compiler_data_in_manifest(project):
    _ = project.contracts
    manifest = project.extract_manifest()
    assert len(manifest.compilers) == 2, manifest.compilers

    vyper_034 = [c for c in manifest.compilers if str(c.version) == str(VERSION_FROM_PRAGMA)][0]
    vyper_028 = [c for c in manifest.compilers if str(c.version) == str(OLDER_VERSION_FROM_PRAGMA)][
        0
    ]

    for compiler in (vyper_028, vyper_034):
        assert compiler.name == "vyper"

    assert len(vyper_034.contractTypes) == 5
    assert len(vyper_028.contractTypes) == 1
    assert "contract" in vyper_034.contractTypes
    assert "older_version" in vyper_028.contractTypes
    for compiler in (vyper_034, vyper_028):
        assert compiler.settings["evmVersion"] == "constantinople"
        assert compiler.settings["optimize"] is True


def test_compile_parse_dev_messages(compiler, dev_revert_source):
    """
    Test parsing of dev messages in a contract. These follow the form of "#dev: ...".

    The compiler will output a map that maps dev messages to line numbers.
    See contract_with_dev_messages.vy for more information.
    """
    result = compiler.compile([dev_revert_source], base_path=PASSING_BASE)

    assert len(result) == 1

    contract = result[0]

    assert contract.dev_messages is not None
    assert len(contract.dev_messages) == 4
    assert contract.dev_messages[6] == "dev: foo"
    assert contract.dev_messages[9] == "dev: bar"
    assert contract.dev_messages[16] == "dev: baz"
    assert contract.dev_messages[20] == "dev: 你好，猿"
    assert 23 not in contract.dev_messages


def test_get_imports(compiler, project):
    vyper_files = [
        x for x in project.contracts_folder.iterdir() if x.is_file() and x.suffix == ".vy"
    ]
    actual = compiler.get_imports(vyper_files)
    builtin_import = "vyper/interfaces/ERC20.json"
    local_import = "interfaces/IFace.vy"
    local_from_import = "interfaces/IFace2.vy"
    dependency_import = "exampledep/Dependency.json"

    assert len(actual["contract.vy"]) == 1
    assert set(actual["contract.vy"]) == {builtin_import}
    assert len(actual["use_iface.vy"]) == 3
    assert set(actual["use_iface.vy"]) == {local_import, local_from_import, dependency_import}
    assert len(actual["use_iface2.vy"]) == 1
    assert set(actual["use_iface2.vy"]) == {local_import}


def test_pc_map(compiler, project):
    """
    Ensure we de-compress the source map correctly by comparing to the results
    from `compile_src()` which includes the uncompressed source map data.
    """

    path = PASSING_BASE / "contract.vy"
    result = compiler.compile([path], base_path=PASSING_BASE)[0]
    actual = result.pcmap.__root__
    code = path.read_text()
    src_map = compile_source(code)["<stdin>"]["source_map"]
    expected = src_map["pc_pos_map"]
    assert actual == expected
