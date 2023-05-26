from pathlib import Path
from typing import List

import pytest
from ape.exceptions import ContractLogicError
from semantic_version import Version  # type: ignore
from vvm import compile_source  # type: ignore
from vvm.exceptions import VyperError  # type: ignore

from ape_vyper.compiler import RuntimeErrorType
from ape_vyper.exceptions import (
    IntegerOverflowError,
    NonPayableError,
    VyperCompileError,
    VyperInstallError,
)

BASE_CONTRACTS_PATH = Path(__file__).parent / "contracts"
PASSING_BASE = BASE_CONTRACTS_PATH / "passing_contracts"
FAILING_BASE = BASE_CONTRACTS_PATH / "failing_contracts"

# Currently, this is the only version specified from a pragma spec
OLDER_VERSION_FROM_PRAGMA = Version("0.2.8")
VERSION_37 = Version("0.3.7")
VERSION_FROM_PRAGMA = Version("0.3.8")


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
    expected_versions = (OLDER_VERSION_FROM_PRAGMA, VERSION_FROM_PRAGMA, VERSION_37)

    for version, sources in actual.items():
        if version in expected_versions:
            continue

        sources = ", ".join([p.name for p in actual[version]])
        fail_message = f"Unexpected version '{version}' with sources: {sources}"
        pytest.fail(fail_message)

    assert len(actual[OLDER_VERSION_FROM_PRAGMA]) == 1
    assert len(actual[VERSION_FROM_PRAGMA]) == 6
    assert actual[OLDER_VERSION_FROM_PRAGMA] == {project.contracts_folder / "older_version.vy"}

    expected = (
        "contract.vy",
        "contract_no_pragma.vy",
        "contract_with_dev_messages.vy",
        "erc20.vy",
        "use_iface.vy",
        "use_iface2.vy",
    )
    names = [x.name for x in actual[VERSION_FROM_PRAGMA]]
    failures = []
    missing = []
    for ex in expected:
        if ex not in names:
            missing.append(ex)

    if missing:
        failures.append(f"Missing contracts: {','.join(missing)}")

    extra = []
    for ac in actual[VERSION_FROM_PRAGMA]:
        if ac.name not in expected:
            extra.append(ac.name)

    if extra:
        failures.append(f"Extra contracts: {', '.join(extra)}")

    assert not failures, "\n".join(failures)


def test_compiler_data_in_manifest(project):
    _ = project.contracts
    manifest = project.extract_manifest()
    assert len(manifest.compilers) == 3, manifest.compilers

    vyper_latest = [c for c in manifest.compilers if str(c.version) == str(VERSION_FROM_PRAGMA)][0]
    vyper_028 = [c for c in manifest.compilers if str(c.version) == str(OLDER_VERSION_FROM_PRAGMA)][
        0
    ]

    for compiler in (vyper_028, vyper_latest):
        assert compiler.name == "vyper"

    assert len(vyper_latest.contractTypes) == 6
    assert len(vyper_028.contractTypes) == 1
    assert "contract" in vyper_latest.contractTypes
    assert "older_version" in vyper_028.contractTypes
    for compiler in (vyper_latest, vyper_028):
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


# TODO: Test this on 0.3.8 and utilize the pcmap from the output.
def test_pc_map(compiler, project):
    """
    Ensure we de-compress the source map correctly by comparing to the results
    from `compile_src()` which includes the uncompressed source map data.
    """

    path = PASSING_BASE / "contract_37.vy"
    result = compiler.compile([path], base_path=PASSING_BASE)[0]
    actual = result.pcmap.__root__
    code = path.read_text()
    compile_result = compile_source(code, vyper_version="0.3.7")["<stdin>"]
    src_map = compile_result["source_map"]
    lines = code.splitlines()

    # Use the old-fashioned way of gathering PCMap to ensure our creative way works
    expected = {pc: {"location": ln} for pc, ln in src_map["pc_pos_map"].items()}
    missing_pcs = []
    empty_locs = []
    wrong_locs = []
    for expected_pc, item_dict in expected.items():
        expected_loc = item_dict["location"]

        # Collect matching locations.
        matching_locs = []
        for mpc, loc in actual.items():
            if loc["location"] == expected_loc:
                matching_locs.append(mpc)

        if expected_pc not in actual:
            missing_pcs.append((expected_pc, expected_loc, matching_locs))
            continue

        if actual[expected_pc]["location"] is None:
            empty_locs.append((expected_pc, expected_loc, matching_locs))
            continue

        if actual[expected_pc]["location"] != expected_loc:
            wrong_locs.append((expected_pc, expected_loc, matching_locs))

    limit = 10  # Only show first ten failures of each category.

    def make_failure(title, ls):
        fail_format = "PC={pc}, Expected={ex} (actual matches={match})"
        suffix = ", ".join([fail_format.format(pc=m, ex=e, match=mat) for m, e, mat in ls[:limit]])
        return f"{title}: {suffix}"

    failures = []
    if len(missing_pcs) != 0:
        failures.append((missing_pcs[0][0], make_failure("Missing PCs", missing_pcs)))
    if len(empty_locs) != 0:
        failures.append((empty_locs[0][0], make_failure("Empty locations", empty_locs)))
    if len(wrong_locs) != 0:
        failures.append((wrong_locs[0][0], make_failure("Wrong locations", wrong_locs)))

    # Show first failures to occur first.
    failures.sort(key=lambda x: x[0])

    assert len(failures) == 0, "\n".join([x[1] for x in failures])

    # Test helper methods.
    def _all(check):
        return [x for x in actual.values() if x.get("dev") == f"dev: {check.value}"]

    def line(cont: str) -> int:
        # A helper for getting expected line numbers
        return [i + 1 for i, x in enumerate(lines) if cont in x][0]

    # Verify non-payable checks.
    nonpayable_checks = _all(RuntimeErrorType.NONPAYABLE_CHECK)
    assert len(nonpayable_checks) == 1

    # Verify integer overflow checks
    overflows = _all(RuntimeErrorType.INTEGER_OVERFLOW)
    overflow_no = line("return (2**127-1) + i")
    assert len(overflows) == 2
    assert overflows[0]["location"] == [overflow_no, 12, overflow_no, 20]

    # Verify integer underflow checks
    underflows = _all(RuntimeErrorType.INTEGER_UNDERFLOW)
    underflow_no = line("return i - (2**127-1)")
    assert len(underflows) == 2
    assert underflows[0]["location"] == [underflow_no, 11, underflow_no, 25]

    # Verify division by zero checks
    div_zeros = _all(RuntimeErrorType.DIVISION_BY_ZERO)
    div_no = line("return 4 / i")
    assert len(div_zeros) == 1
    assert div_zeros[0]["location"] == [div_no, 11, div_no, 16]

    # Verify modulo by zero checks
    mod_zeros = _all(RuntimeErrorType.MODULO_BY_ZERO)
    mod_no = line("return 4 % i")
    assert len(mod_zeros) == 1
    assert mod_zeros[0]["location"] == [mod_no, 11, mod_no, 16]

    # Verify index out of range checks
    range_checks = _all(RuntimeErrorType.INDEX_OUT_OF_RANGE)
    range_no = line("return self.dynArray[idx]")
    assert len(range_checks) == 1
    assert range_checks[0]["location"] == [range_no, 11, range_no, 24]


def test_int_overflow(geth_provider, contract, account):
    int_max = 2**256 - 1
    with pytest.raises(IntegerOverflowError):
        contract.addBalance(int_max, sender=account)


def test_non_payable_check(geth_provider, contract, account):
    with pytest.raises(NonPayableError):
        contract.addBalance(123, sender=account, value=1)


# TODO: Run this test using 0.3.8 as well, once we get the PCMap working.
def test_trace_source(account, geth_provider, project, contract):
    receipt = contract.addBalance(123, sender=account)
    actual = receipt.source_traceback
    base_folder = project.contracts_folder
    expected = rf"""
Traceback (most recent call last)
  File {base_folder}/traceback_contract.vy, in addBalance
       27     # Comments in the middle (is a test)
       28
       29     for i in [1, 2, 3, 4, 5]:
       30         if i != num:
       31             continue
       32
  -->  33     return self._balance
""".strip()
    assert str(actual) == expected


def test_trace_err_source(account, geth_provider, project, contract):
    txn = contract.addBalance_f.as_transaction(123)
    try:
        account.call(txn)
    except ContractLogicError:
        pass

    receipt = geth_provider.get_receipt(txn.txn_hash.hex())
    actual = receipt.source_traceback
    base_folder = project.contracts_folder
    expected = rf"""
Traceback (most recent call last)
  File {base_folder}/traceback_contract.vy, in addBalance_f
       44     # Run some loops.
       45     for i in [1, 2, 3, 4, 5]:
       46         if i == num:
       47             break
       48
       49     # Fail in the middle (is test)
       50     # Fails because was already set above.
  -->  51     self.registry.register_f(msg.sender)
       52
       53     for i in [1, 2, 3, 4, 5]:
       54         if i != num:
       55             continue

  File {base_folder}/registry.vy, in register_f
  -->  12     assert self.addr != addr, "doubling."
       13     self.addr = addr
    """.strip()
    assert str(actual) == expected
