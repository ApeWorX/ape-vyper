import re

import pytest
from ape.exceptions import ContractLogicError
from packaging.version import Version
from vvm import compile_source  # type: ignore
from vvm.exceptions import VyperError  # type: ignore

from ape_vyper.compiler import RuntimeErrorType
from ape_vyper.exceptions import (
    FallbackNotDefinedError,
    IntegerOverflowError,
    InvalidCalldataOrValueError,
    NonPayableError,
    VyperCompileError,
    VyperInstallError,
)

# Currently, this is the only version specified from a pragma spec
from .conftest import FAILING_BASE, FAILING_CONTRACT_NAMES, PASSING_CONTRACT_NAMES, TEMPLATES

OLDER_VERSION_FROM_PRAGMA = Version("0.2.16")
VERSION_37 = Version("0.3.7")
VERSION_FROM_PRAGMA = Version("0.3.9")


@pytest.fixture
def dev_revert_source(project):
    return project.contracts_folder / "contract_with_dev_messages.vy"


EXPECTED_FAIL_PATTERNS = {
    "contract_undeclared_variable": re.compile(
        (
            r"\w*\.vy\s*UndeclaredDefinition:'\w*' "
            r'has not been declared\s*contract "\w*.vy"'
            # NOTE: Following bit proves we have line numbers (see all the \d's).
            r', function "\w*",.*\w*\d:\d\s*\d def.*\s*.*\d\s*\w* = \w*\s*-*\^\s*\d\s*.*'
        ),
    ),
    "contract_unknown_pragma": "",
}


def test_compile_project(project):
    contracts = project.load_contracts()
    assert len(contracts) == len(
        [p.name for p in project.contracts_folder.glob("*.vy") if p.is_file()]
    )
    assert contracts["contract_039"].source_id == "contract_039.vy"
    assert contracts["contract_no_pragma"].source_id == "contract_no_pragma.vy"
    assert contracts["older_version"].source_id == "older_version.vy"


@pytest.mark.parametrize("contract_name", PASSING_CONTRACT_NAMES)
def test_compile_individual_contracts(project, contract_name, compiler):
    path = project.contracts_folder / contract_name
    assert compiler.compile([path])


@pytest.mark.parametrize(
    "contract_name", [n for n in FAILING_CONTRACT_NAMES if n != "contract_unknown_pragma.vy"]
)
def test_compile_failures(contract_name, compiler):
    path = FAILING_BASE / contract_name
    with pytest.raises(VyperCompileError, match=EXPECTED_FAIL_PATTERNS[path.stem]) as err:
        compiler.compile([path], base_path=FAILING_BASE)

    assert isinstance(err.value.base_err, VyperError)


def test_install_failure(compiler):
    path = FAILING_BASE / "contract_unknown_pragma.vy"
    with pytest.raises(VyperInstallError, match="No available version to install."):
        compiler.compile([path])


def test_get_version_map(project, compiler, all_versions):
    vyper_files = [
        x for x in project.contracts_folder.iterdir() if x.is_file() and x.suffix == ".vy"
    ]
    actual = compiler.get_version_map(vyper_files)
    expected_versions = [Version(v) for v in all_versions]

    for version, sources in actual.items():
        if version in expected_versions:
            continue

        sources = ", ".join([p.name for p in actual[version]])
        fail_message = f"Unexpected version '{version}' with sources: {sources}"
        pytest.fail(fail_message)

    assert len(actual[OLDER_VERSION_FROM_PRAGMA]) >= 1
    assert len(actual[VERSION_FROM_PRAGMA]) >= 1
    assert project.contracts_folder / "older_version.vy" in actual[OLDER_VERSION_FROM_PRAGMA]

    expected = [
        "contract_with_dev_messages.vy",
        "erc20.vy",
        "use_iface.vy",
        "use_iface2.vy",
    ]

    # Add the 0.3.9 contracts.
    for template in TEMPLATES:
        expected.append(f"{template}_039.vy")

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
    assert len(manifest.compilers) >= 3, manifest.compilers

    vyper_latest = [c for c in manifest.compilers if str(c.version) == str(VERSION_FROM_PRAGMA)][0]
    vyper_028 = [c for c in manifest.compilers if str(c.version) == str(OLDER_VERSION_FROM_PRAGMA)][
        0
    ]

    for compiler in (vyper_028, vyper_latest):
        assert compiler.name == "vyper"

    assert len(vyper_latest.contractTypes) >= 9
    assert len(vyper_028.contractTypes) >= 1
    assert "contract_039" in vyper_latest.contractTypes
    assert "older_version" in vyper_028.contractTypes
    for compiler in (vyper_latest, vyper_028):
        assert compiler.settings["evmVersion"] == "istanbul"
        assert compiler.settings["optimize"] is True


def test_compile_parse_dev_messages(compiler, dev_revert_source, project):
    """
    Test parsing of dev messages in a contract. These follow the form of "#dev: ...".

    The compiler will output a map that maps dev messages to line numbers.
    See contract_with_dev_messages.vy for more information.
    """
    result = compiler.compile([dev_revert_source], base_path=project.contracts_folder)

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

    assert len(actual["contract_037.vy"]) == 1
    assert set(actual["contract_037.vy"]) == {builtin_import}
    assert len(actual["use_iface.vy"]) == 3
    assert set(actual["use_iface.vy"]) == {local_import, local_from_import, dependency_import}
    assert len(actual["use_iface2.vy"]) == 1
    assert set(actual["use_iface2.vy"]) == {local_import}


@pytest.mark.parametrize("src,vers", [("contract_039", "0.3.9"), ("contract_037", "0.3.7")])
def test_pc_map(compiler, project, src, vers):
    """
    Ensure we de-compress the source map correctly by comparing to the results
    from `compile_src()` which includes the uncompressed source map data.
    """

    path = project.contracts_folder / f"{src}.vy"
    result = compiler.compile([path], base_path=project.contracts_folder)[0]
    actual = result.pcmap.__root__
    code = path.read_text()
    compile_result = compile_source(code, vyper_version=vers, evm_version=compiler.evm_version)[
        "<stdin>"
    ]
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
    if nonpayable_checks:
        assert len(nonpayable_checks) >= 1
    else:
        # NOTE: Vyper 0.3.10rc3 doesn't have these anymore.
        # But they do have a new error type instead.
        checks = _all(RuntimeErrorType.INVALID_CALLDATA_OR_VALUE)
        assert len(checks) >= 1

    # Verify integer overflow checks
    overflows = _all(RuntimeErrorType.INTEGER_OVERFLOW)
    overflow_no = line("return (2**127-1) + i")
    expected_overflow_loc = [overflow_no, 12, overflow_no, 20]
    assert len(overflows) >= 2

    if vers == "0.3.7":
        assert expected_overflow_loc in [o["location"] for o in overflows if o["location"]]
    # else: 0.3.9 registers as IntegerBoundsCheck

    # Verify integer underflow checks
    underflows = _all(RuntimeErrorType.INTEGER_UNDERFLOW)
    underflow_no = line("return i - (2**127-1)")
    expected_underflow_loc = [underflow_no, 11, underflow_no, 25]
    assert len(underflows) >= 2

    if vers == "0.3.7":
        assert expected_underflow_loc in [u["location"] for u in underflows if u["location"]]
    # else: 0.3.9 registers as IntegerBoundsCheck

    # Verify division by zero checks
    div_zeros = _all(RuntimeErrorType.DIVISION_BY_ZERO)
    div_no = line("return 4 / i")
    expected_div_0 = [div_no, 11, div_no, 16]

    if vers == "0.3.7":
        assert len(div_zeros) >= 1
        assert expected_div_0 in [d["location"] for d in div_zeros if d["location"]]
    # TODO: figure out how to detect these on 0.3.9

    # Verify modulo by zero checks
    mod_zeros = _all(RuntimeErrorType.MODULO_BY_ZERO)
    mod_no = line("return 4 % i")
    expected_mod_0_loc = [mod_no, 11, mod_no, 16]
    assert len(mod_zeros) >= 1
    assert expected_mod_0_loc in [m["location"] for m in mod_zeros if m["location"]]

    # Verify index out of range checks
    range_checks = _all(RuntimeErrorType.INDEX_OUT_OF_RANGE)
    range_no = line("return self.dynArray[idx]")
    expected_range_check = [range_no, 11, range_no, 24]
    if vers == "0.3.7":
        assert len(range_checks) >= 1
        assert expected_range_check in [r["location"] for r in range_checks]
    # TODO: figure out how to detect these on 0.3.9


def test_enrich_error_int_overflow(geth_provider, traceback_contract, account):
    int_max = 2**256 - 1
    with pytest.raises(IntegerOverflowError):
        traceback_contract.addBalance(int_max, sender=account)


def test_enrich_error_non_payable_check(geth_provider, traceback_contract, account):
    if traceback_contract.contract_type.name.endswith("0310rc3"):
        # NOTE: Nonpayable error is combined with calldata check now.
        with pytest.raises(InvalidCalldataOrValueError):
            traceback_contract.addBalance(123, sender=account, value=1)

    else:
        with pytest.raises(NonPayableError):
            traceback_contract.addBalance(123, sender=account, value=1)


def test_enrich_error_fallback(geth_provider, traceback_contract, account):
    """
    Show that when attempting to call a contract's fallback method when there is
    no fallback defined results in a custom contract logic error.
    """
    with pytest.raises(FallbackNotDefinedError):
        traceback_contract(sender=account)


def test_enrich_error_handle_when_name(compiler, geth_provider, mocker):
    """
    Sometimes, a provider may use the name of the enum instead of the value,
    which we are still able to enrich.
    """

    tb = mocker.MagicMock()
    tb.revert_type = "NONPAYABLE_CHECK"
    error = ContractLogicError("", source_traceback=tb)
    new_error = compiler.enrich_error(error)
    assert isinstance(new_error, NonPayableError)


@pytest.mark.parametrize("arguments", [(), (123,), (123, 321)])
def test_trace_source(account, geth_provider, project, traceback_contract, arguments):
    receipt = traceback_contract.addBalance(*arguments, sender=account)
    actual = receipt.source_traceback
    base_folder = project.contracts_folder
    contract_name = traceback_contract.contract_type.name
    expected = rf"""
Traceback (most recent call last)
  File {base_folder}/{contract_name}.vy, in addBalance
       32         if i != num:
       33             continue
       34
  -->  35     return self._balance
""".strip()
    assert str(actual) == expected


def test_trace_source_content_from_kwarg_default_parametrization(
    account, geth_provider, project, traceback_contract
):
    """
    This test is for verifying stuff around Vyper auto-generated methods from kwarg defaults.
    Mostly, need to make sure the correct content is discoverable in the source traceback
    so that coverage works properly.
    """
    no_args_tx = traceback_contract.addBalance(sender=account)
    no_args_tb = no_args_tx.source_traceback

    def check(name: str, tb):
        items = [x.closure.full_name for x in tb if x.closure.full_name == name]
        assert len(items) >= 1

    check("addBalance()", no_args_tb)

    single_arg_tx = traceback_contract.addBalance(442, sender=account)
    single_arg_tb = single_arg_tx.source_traceback
    check("addBalance(uint256)", single_arg_tb)

    both_args_tx = traceback_contract.addBalance(4, 5, sender=account)
    both_args_tb = both_args_tx.source_traceback
    check("addBalance(uint256,uint256)", both_args_tb)


def test_trace_err_source(account, geth_provider, project, traceback_contract):
    txn = traceback_contract.addBalance_f.as_transaction(123)
    try:
        account.call(txn)
    except ContractLogicError:
        pass

    receipt = geth_provider.get_receipt(txn.txn_hash.hex())
    actual = receipt.source_traceback
    base_folder = project.contracts_folder
    contract_name = traceback_contract.contract_type.name
    version_key = contract_name.split("traceback_contract_")[-1]
    expected = rf"""
Traceback (most recent call last)
  File {base_folder}/{contract_name}.vy, in addBalance_f
       48         if i == num:
       49             break
       50
       51     # Fail in the middle (is test)
       52     # Fails because was already set above.
  -->  53     self.registry.register_f(msg.sender)
       54
       55     for i in [1, 2, 3, 4, 5]:
       56         if i != num:
       57             continue

  File {base_folder}/registry_{version_key}.vy, in register_f
       11 def register_f(addr: address):
  -->  12     assert self.addr != addr, "doubling."
       13     self.addr = addr
    """.strip()
    assert str(actual) == expected


def test_trace_source_default_method(geth_provider, account, project):
    """
    This test proves you get a working source-traceback from __default__ calls.
    """
    contract = project.non_payable_default.deploy(sender=account)
    receipt = contract(sender=account)
    src_tb = receipt.source_traceback
    actual = str(src_tb[-1][-1]).lstrip()  # Last line in traceback (without indent).
    expected = "8     log NotPayment(msg.sender)"
    assert actual == expected
