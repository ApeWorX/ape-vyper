from pathlib import Path
from typing import List

import pytest
from ape.types import LineTraceNode
from semantic_version import Version  # type: ignore
from vvm.exceptions import VyperError  # type: ignore

from ape_vyper import EXTENSIONS
from ape_vyper.exceptions import VyperCompileError, VyperInstallError

BASE_CONTRACTS_PATH = Path(__file__).parent / "contracts"

# Currently, this is the only version specified from a pragma spec
OLDER_VERSION_FROM_PRAGMA = Version("0.2.8")
VERSION_FROM_PRAGMA = Version("0.3.7")


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
    assert len(contracts) == len(
        [p.name for p in (BASE_CONTRACTS_PATH / "passing_contracts").glob("*.vy") if p.is_file()]
    )
    assert contracts["contract"].source_id == "contract.vy"
    assert contracts["contract_no_pragma"].source_id == "contract_no_pragma.vy"
    assert contracts["older_version"].source_id == "older_version.vy"


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
    vyper_files = [
        x for x in project.contracts_folder.iterdir() if x.is_file() and x.suffix in EXTENSIONS
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


def test_compile_parse_dev_messages(compiler):
    """
    Test parsing of dev messages in a contract. These follow the form of "#dev: ...".

    The compiler will output a map that maps dev messages to line numbers.
    See contract_with_dev_messages.vy for more information.
    """
    path = BASE_CONTRACTS_PATH / "passing_contracts" / "contract_with_dev_messages.vy"

    result = compiler.compile([path])

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
        x for x in project.contracts_folder.iterdir() if x.is_file() and x.suffix in EXTENSIONS
    ]
    actual = compiler.get_imports(vyper_files)
    assert actual["use_iface.vy"] == ["interfaces/IFace.vy"]
    assert actual["use_iface2.vy"] == ["interfaces/IFace.vy"]


def test_line_trace(accounts, project, geth_provider):
    owner = accounts.test_accounts[0]
    registry = project.registry.deploy(sender=owner)
    contract = owner.deploy(project.contract, registry)
    receipt = contract.foo2(123, owner, sender=owner)
    actual = receipt.line_trace
    assert actual == [
        LineTraceNode(
            source_id="contract.vy",
            method_id="foo2(a: uint256, b: address) -> uint256",
            lines={
                31: '    assert a != 0, "zero"  # TEST COMMENT 5 def foo2():',
                32: "    self.registry.register(b)  # TEST COMMENT 6 def foo2():",
            },
        ),
        LineTraceNode(
            source_id="registry.vy",
            method_id="register(addr: address)",
            lines={7: "    assert addr != self.addr", 8: "    self.addr = addr"},
        ),
        LineTraceNode(
            source_id="contract.vy",
            method_id="foo2(a: uint256, b: address) -> uint256",
            lines={
                32: "    self.registry.register(b)  # TEST COMMENT 6 def foo2():",
                33: "    self.bar1 = self.baz(a)  # TEST COMMENT 7 def foo2():",
            },
        ),
        LineTraceNode(
            source_id="contract.vy",
            method_id="baz(a: uint256) -> uint256",
            lines={50: "    return a + 123"},
        ),
        LineTraceNode(
            source_id="contract.vy",
            method_id="foo2(a: uint256, b: address) -> uint256",
            lines={
                33: "    self.bar1 = self.baz(a)  # TEST COMMENT 7 def foo2():",
                34: "    log FooHappened(self.bar1)  # TEST COMMENT 8 def foo2():",
                37: "    for i in [1, 2, 3, 4, 5]:",
                38: "        if i == a:",
                39: "            break",
                41: "    for i in [1, 2, 3, 4, 5]:",
                42: "        if i != a:",
                43: "            continue",
                45: "    return self.bar1  # TEST COMMENT 9 def foo2():",
            },
        ),
    ]
