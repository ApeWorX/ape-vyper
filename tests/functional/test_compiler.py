import re
from pathlib import Path
from typing import Optional

import ape
import pytest
import vvm  # type: ignore
from ape.exceptions import CompilerError, ContractLogicError
from ape.types import SourceTraceback
from ape.utils import get_full_extension
from ethpm_types import ContractType
from packaging.version import Version
from vvm.exceptions import VyperError  # type: ignore

from ape_vyper._utils import EVM_VERSION_DEFAULT
from ape_vyper.exceptions import (
    FallbackNotDefinedError,
    IntegerOverflowError,
    InvalidCalldataOrValueError,
    NonPayableError,
    RuntimeErrorType,
    VyperCompileError,
    VyperInstallError,
)

# Currently, this is the only version specified from a pragma spec
from ..conftest import FAILING_BASE, FAILING_CONTRACT_NAMES, PASSING_CONTRACT_NAMES, TEMPLATES

OLDER_VERSION_FROM_PRAGMA = Version("0.2.16")
VERSION_37 = Version("0.3.7")
VERSION_FROM_PRAGMA = Version("0.3.10")
VERSION_04 = Version("0.4.0")


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
ZERO_FOUR_CONTRACT_FLAT = """
# pragma version ~=0.4.0


interface IFaceZeroFour:
    def implementThisPlease(role: bytes32) -> bool: view


# @dev Returns the address of the current owner.
# @notice If you declare a variable as `public`,
# Vyper automatically generates an `external`
# getter function for the variable.
owner: public(address)


# @dev Emitted when the ownership is transferred
# from `previous_owner` to `new_owner`.
event OwnershipTransferred:
    previous_owner: indexed(address)
    new_owner: indexed(address)


@deploy
@payable
def __init__():
    \"\"\"
    @dev To omit the opcodes for checking the `msg.value`
         in the creation-time EVM bytecode, the constructor
         is declared as `payable`.
    @notice The `owner` role will be assigned to
            the `msg.sender`.
    \"\"\"
    self._transfer_ownership(msg.sender)


@external
def transfer_ownership(new_owner: address):
    \"\"\"
    @dev Transfers the ownership of the contract
         to a new account `new_owner`.
    @notice Note that this function can only be
            called by the current `owner`. Also,
            the `new_owner` cannot be the zero address.
    @param new_owner The 20-byte address of the new owner.
    \"\"\"
    self._check_owner()
    assert new_owner != empty(address), "ownable: new owner is the zero address"
    self._transfer_ownership(new_owner)


@external
def renounce_ownership():
    \"\"\"
    @dev Leaves the contract without an owner.
    @notice Renouncing ownership will leave the
            contract without an owner, thereby
            removing any functionality that is
            only available to the owner.
    \"\"\"
    self._check_owner()
    self._transfer_ownership(empty(address))


@internal
def _check_owner():
    \"\"\"
    @dev Throws if the sender is not the owner.
    \"\"\"
    assert msg.sender == self.owner, "ownable: caller is not the owner"


@internal
def _transfer_ownership(new_owner: address):
    \"\"\"
    @dev Transfers the ownership of the contract
         to a new account `new_owner`.
    @notice This is an `internal` function without
            access restriction.
    @param new_owner The 20-byte address of the new owner.
    \"\"\"
    old_owner: address = self.owner
    self.owner = new_owner
    log OwnershipTransferred(old_owner, new_owner)


# Showing importing interface from module.
interface Ballot:
    def delegated(addr: address) -> bool: view

@internal
def moduleMethod2() -> bool:
    return True


# This source is also imported from `zero_four.py` to test
# multiple imports across sources during flattening.

@internal
def moduleMethod() -> bool:
    return True


@external
def callModule2FunctionFromAnotherSource(role: bytes32) -> bool:
    return self.moduleMethod2()


implements: IFaceZeroFour


# Also show we can import from ethereum namespace.
# (new in Vyper 0.4).

# `self.vy` also imports this next line.
# We are testing that the flattener can handle that.

@external
@view
def implementThisPlease(role: bytes32) -> bool:
    return True


@external
def callModuleFunction(role: bytes32) -> bool:
    return self.moduleMethod()


@external
def callModule2Function(role: bytes32) -> bool:
    return self.moduleMethod2()
""".lstrip()


def test_compile_project(project):
    actual = sorted(list(project.load_contracts().keys()))

    # NOTE: Ignore interfaces for this test.
    expected = sorted(
        [
            p.stem
            for p in project.contracts_folder.rglob("*.vy")
            if p.is_file() and not p.name.startswith("I")
        ]
    )
    if missing := [e for e in expected if e not in actual]:
        missing_str = ", ".join(missing)
        pytest.xfail(f"Missing the following expected sources: {missing_str}")
    if extra := [a for a in actual if a not in expected]:
        extra_str = ", ".join(extra)
        pytest.xfail(f"Received the following extra sources: {extra_str}")

    assert "contract_039" in actual
    assert "contract_no_pragma" in actual
    assert "older_version" in actual


@pytest.mark.parametrize("contract_name", PASSING_CONTRACT_NAMES)
def test_compile_individual_contracts(project, contract_name, compiler):
    path = project.contracts_folder / contract_name
    assert list(compiler.compile((path,), project=project))


@pytest.mark.parametrize(
    "contract_name", [n for n in FAILING_CONTRACT_NAMES if n != "contract_unknown_pragma.vy"]
)
def test_compile_failures(contract_name, compiler):
    failing_project = ape.Project(FAILING_BASE)
    path = FAILING_BASE / contract_name
    with pytest.raises(VyperCompileError, match=EXPECTED_FAIL_PATTERNS[path.stem]) as err:
        list(compiler.compile((path,), project=failing_project))

    assert isinstance(err.value.base_err, VyperError)


def test_compile_zero_four(compiler, project):
    """
    An easy way to test only Vyper 0.4 changes.
    """
    paths = (
        project.contracts_folder / "subdir" / "zero_four_in_subdir.vy",
        project.contracts_folder / "zero_four.vy",
    )
    result = [x.name for x in compiler.compile(paths, project=project)]
    assert "zero_four" in result
    assert "zero_four_in_subdir" in result


def test_install_failure(compiler):
    failing_project = ape.Project(FAILING_BASE)
    path = FAILING_BASE / "contract_unknown_pragma.vy"
    with pytest.raises(VyperInstallError, match="No available version to install."):
        list(compiler.compile((path,), project=failing_project))


def test_get_version_map(project, compiler, all_versions):
    vyper_files = [
        x
        for x in project.contracts_folder.iterdir()
        if x.is_file() and get_full_extension(x) == ".vy"
    ]
    actual = compiler.get_version_map(vyper_files, project=project)
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
        "optimize_codesize.vy",
        "evm_pragma.vy",
        "use_iface2.vy",
        "pragma_with_space.vy",
        "flatten_me.vy",
    ]

    # Add the 0.3.10 contracts.
    for template in TEMPLATES:
        expected.append(f"{template}_0310.vy")

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

    # Vyper 0.4.0 assertions.
    actual4 = {x.name for x in actual[VERSION_04]}
    expected4 = {
        "contract_no_pragma.vy",
        "empty.vy",
        "zero_four.vy",
        "zero_four_module.vy",
        "zero_four_module_2.vy",
        "zero_four_snekmate_erc20.vy",
    }
    assert actual4 == expected4


def test_compiler_data_in_manifest(project):
    def run_test(manifest):
        assert len(manifest.compilers) >= 3, manifest.compilers

        all_latest_03 = [
            c for c in manifest.compilers if str(c.version) == str(VERSION_FROM_PRAGMA)
        ]
        evm_opt = [c for c in all_latest_03 if c.settings.get("evmVersion") == "paris"][0]
        gas_opt = [c for c in all_latest_03 if c.settings["optimize"] == "gas"][0]
        true_opt = [
            c
            for c in manifest.compilers
            if c.settings["optimize"] is True and "non_payable_default" in c.contractTypes
        ][0]
        codesize_opt = [
            c
            for c in all_latest_03
            if c.settings["optimize"] == "codesize" and c.settings.get("evmVersion") != "paris"
        ][0]
        vyper_028 = [
            c for c in manifest.compilers if str(c.version) == str(OLDER_VERSION_FROM_PRAGMA)
        ][0]

        for compiler in (vyper_028, codesize_opt):
            assert compiler.name == "vyper"

        assert vyper_028.settings["evmVersion"] == "berlin"
        assert codesize_opt.settings["evmVersion"] == "shanghai"

        # There is only one contract with evm-version pragma.
        assert evm_opt.contractTypes == ["evm_pragma"]
        assert evm_opt.settings.get("evmVersion") == "paris"

        assert "optimize_codesize" in codesize_opt.contractTypes
        assert "older_version" in vyper_028.contractTypes
        assert len(gas_opt.contractTypes) >= 1
        assert "non_payable_default" in true_opt.contractTypes

    project.update_manifest(compilers=[])
    project.load_contracts(use_cache=False)
    run_test(project.manifest)
    man = project.extract_manifest()
    run_test(man)


def test_compile_parse_dev_messages(compiler, dev_revert_source, project):
    """
    Test parsing of dev messages in a contract. These follow the form of "#dev: ...".

    The compiler will output a map that maps dev messages to line numbers.
    See contract_with_dev_messages.vy for more information.
    """
    result = list(compiler.compile((dev_revert_source,), project=project))

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
    # Ensure the dependency starts off un-compiled so we can show this
    # is the point at which it will be compiled. We make sure to only
    # compile when we know it is a JSON interface based dependency
    # and not a site-package or relative-path based dependency.
    dependency = project.dependencies["exampledependency"]["local"]
    dependency.manifest.contract_types = {}

    vyper_files = [
        x for x in project.contracts_folder.iterdir() if x.is_file() and x.suffix == ".vy"
    ]
    actual = compiler.get_imports(vyper_files, project=project)

    prefix = "tests/contracts/passing_contracts"
    builtin_import = "vyper/interfaces/ERC20.json"
    local_import = "IFace.vy"
    local_from_import = "IFace2.vy"
    local_nested_import = "IFaceNested.vy"
    dependency_import = "Dependency.vy"

    # The source IDs end up as absolute paths because they are in tempdir
    # (not direct local project) and because of Vyper 0.4 reasons, we need
    # this to be the case. And we don't know the version map yet at this point.
    contract_37_key = [k for k in actual if f"{prefix}/contract_037.vy" in k][0]
    use_iface_key = [k for k in actual if f"{prefix}/use_iface.vy" in k][0]
    use_iface2_key = [k for k in actual if f"{prefix}/use_iface2.vy" in k][0]

    assert set(actual[contract_37_key]) == {builtin_import}

    actual_iface_use = actual[use_iface_key]
    for expected in (local_import, local_from_import, dependency_import, local_nested_import):
        assert any(k for k in actual_iface_use if expected in k), f"{expected} not found"

    assert actual[use_iface2_key][0].endswith(local_import)


@pytest.mark.parametrize("src,vers", [("contract_039", "0.3.9"), ("contract_037", "0.3.7")])
def test_pc_map(compiler, project, src, vers):
    """
    Ensure we de-compress the source map correctly by comparing to the results
    from `compile_src()` which includes the uncompressed source map data.
    """

    path = project.sources.lookup(src)
    result = list(compiler.compile((path,), project=project))[0]
    actual = result.pcmap.root
    code = path.read_text(encoding="utf8")
    vvm.install_vyper(vers)
    cfg = compiler.get_config(project=project)
    evm_version = cfg.evm_version
    compile_result = vvm.compile_source(code, vyper_version=vers, evm_version=evm_version)
    std_result = compile_result["<stdin>"]
    src_map = std_result["source_map"]
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
        # NOTE: Vyper 0.3.10 doesn't have these anymore.
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
    if traceback_contract.contract_type.name.endswith("0310"):
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

    class TB(SourceTraceback):
        @property
        def revert_type(self) -> Optional[str]:
            return "NONPAYABLE_CHECK"

    tb = TB([{"statements": [], "closure": {"name": "fn"}, "depth": 0}])  # type: ignore
    error = ContractLogicError(None, source_traceback=tb)
    new_error = compiler.enrich_error(error)
    assert isinstance(new_error, NonPayableError)


def test_trace_source(geth_provider, project, traceback_contract, account, compiler):
    receipt = traceback_contract.addBalance(123, sender=account)
    contract = project._create_contract_source(traceback_contract.contract_type)
    trace = receipt.trace
    actual = compiler.trace_source(contract, trace, receipt.data)
    base_folder = Path(__file__).parent.parent / "contracts" / "passing_contracts"
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


@pytest.mark.parametrize("arguments", [(), (123,), (123, 321)])
def test_trace_source_from_receipt(account, geth_provider, project, traceback_contract, arguments):
    receipt = traceback_contract.addBalance(*arguments, sender=account)
    actual = receipt.source_traceback
    base_folder = Path(__file__).parent.parent / "contracts" / "passing_contracts"
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


def test_trace_source_when_err(account, geth_provider, project, traceback_contract):
    txn = traceback_contract.addBalance_f.as_transaction(123)
    try:
        account.call(txn)
    except ContractLogicError:
        pass

    receipt = geth_provider.get_receipt(txn.txn_hash.hex())
    actual = receipt.source_traceback
    base_folder = Path(__file__).parent.parent / "contracts" / "passing_contracts"
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


def test_compile_with_version_set_in_config(config, projects_path, compiler, mocker):
    path = projects_path / "version_in_config"
    version_from_config = "0.3.7"
    spy = mocker.patch("ape_vyper.compiler._versions.base.vvm_compile_standard")
    project = ape.Project(path)

    contract = project.contracts_folder / "v_contract.vy"
    settings = compiler.get_compiler_settings((contract,), project=project)
    assert str(list(settings.keys())[0]) == version_from_config

    # Show it uses this version in the compiler.
    project.load_contracts(use_cache=False)
    assert str(spy.call_args[1]["vyper_version"]) == version_from_config


def test_compile_code(project, compiler, dev_revert_source):
    code = dev_revert_source.read_text(encoding="utf8")
    actual = compiler.compile_code(code, project=project, contractName="MyContract")
    assert isinstance(actual, ContractType)
    assert actual.name == "MyContract"
    assert len(actual.abi) > 1
    assert len(actual.deployment_bytecode.bytecode) > 1
    assert len(actual.runtime_bytecode.bytecode) > 1

    # Ensure temp-file was deleted.
    file = project.path / "MyContract.vy"
    assert not file.is_file()


def test_compile_with_version_set_in_settings_dict(config, compiler_manager, projects_path):
    path = projects_path / "version_in_config"
    contract = path / "contracts" / "v_contract.vy"
    project = ape.Project(path)
    expected = '.*Version specification "0.3.10" is not compatible with compiler version "0.3.3"'
    iterator = compiler_manager.compile(
        (contract,), project=project, settings={"vyper": {"version": "0.3.3"}}
    )
    with pytest.raises(CompilerError, match=expected):
        _ = list(iterator)


@pytest.mark.parametrize(
    "contract_name",
    [
        # This first one has most known edge cases
        "flatten_me.vy",
        # Test on the below for general compatibility.
        "contract_with_dev_messages.vy",
        "erc20.vy",
        "use_iface.vy",
        "optimize_codesize.vy",
        "evm_pragma.vy",
        "use_iface2.vy",
        "contract_no_pragma.vy",  # no pragma should compile with latest version
        "empty.vy",  # empty file still compiles with latest version
        "pragma_with_space.vy",
    ],
)
def test_flatten_contract(all_versions, project, contract_name, compiler):
    path = project.contracts_folder / contract_name
    source = compiler.flatten_contract(path, project=project)

    # Ensure it also compiles.
    source_code = str(source)
    version = compiler._source_vyper_version(source_code)
    vvm.install_vyper(str(version))
    vvm.compile_source(source_code, base_path=project.path, vyper_version=version)


def test_flatten_contract_04(project, compiler):
    path = project.contracts_folder / "zero_four.vy"
    source = compiler.flatten_contract(path, project=project)
    source_code = str(source)
    assert source_code == ZERO_FOUR_CONTRACT_FLAT

    # Ensure it also compiles.
    version = compiler._source_vyper_version(source_code)
    vvm.install_vyper(str(version))
    vvm.compile_source(source_code, base_path=project.path, vyper_version=version)


def test_get_import_remapping(project, compiler):
    dependency = project.dependencies["exampledependency"]["local"]
    dependency.manifest.contract_types = {}

    # Getting import remapping does not compile on its own!
    # This is important because we don't necessarily want to
    # compile every dependency, only the ones with imports
    # that indicate this.
    actual = compiler.get_import_remapping(project=project)
    assert actual == {}

    dependency.load_contracts()
    actual = compiler.get_import_remapping(project=project)
    assert "exampledependency/Dependency.json" in actual


def test_get_compiler_settings(project, compiler):
    vyper2_path = project.contracts_folder / "older_version.vy"
    vyper3_path = project.contracts_folder / "non_payable_default.vy"
    vyper4_path = project.contracts_folder / "zero_four.vy"
    vyper2_settings = compiler.get_compiler_settings((vyper2_path,), project=project)
    vyper3_settings = compiler.get_compiler_settings((vyper3_path,), project=project)
    vyper4_settings = compiler.get_compiler_settings((vyper4_path,), project=project)

    v2_version_used = next(iter(vyper2_settings.keys()))
    assert v2_version_used >= Version("0.2.16"), f"version={v2_version_used}"
    assert vyper2_settings[v2_version_used]["true%berlin"]["optimize"] is True
    assert vyper2_settings[v2_version_used]["true%berlin"]["evmVersion"] == "berlin"
    assert vyper2_settings[v2_version_used]["true%berlin"]["outputSelection"] == {
        "tests/contracts/passing_contracts/older_version.vy": ["*"]
    }
    assert "enable_decimals" not in vyper2_settings[v2_version_used]["true%berlin"]

    v3_version_used = next(iter(vyper3_settings.keys()))
    settings_key = next(iter(vyper3_settings[v3_version_used].keys()))
    valid_evm_versions = [
        v for k, v in EVM_VERSION_DEFAULT.items() if Version(k) >= Version("0.3.0")
    ]
    pattern = rf"true%({'|'.join(valid_evm_versions)})"
    assert re.match(pattern, settings_key)
    assert v3_version_used >= Version("0.3.0"), f"version={v3_version_used}"
    assert vyper3_settings[v3_version_used][settings_key]["optimize"] is True
    assert vyper3_settings[v3_version_used][settings_key]["evmVersion"] in valid_evm_versions
    assert vyper3_settings[v3_version_used][settings_key]["outputSelection"] == {
        "tests/contracts/passing_contracts/non_payable_default.vy": ["*"]
    }
    assert "enable_decimals" not in vyper3_settings[v3_version_used][settings_key]

    assert len(vyper4_settings) == 1, f"extra keys={''.join([f'{x}' for x in vyper4_settings])}"
    v4_version_used = next(iter(vyper4_settings.keys()))
    assert v4_version_used >= Version(
        "0.4.0"
    ), f"version={v4_version_used} full_data={vyper4_settings}"
    assert vyper4_settings[v4_version_used]["gas%shanghai"]["enable_decimals"] is True
    assert vyper4_settings[v4_version_used]["gas%shanghai"]["optimize"] == "gas"
    assert vyper4_settings[v4_version_used]["gas%shanghai"]["outputSelection"] == {
        "tests/contracts/passing_contracts/zero_four.vy": ["*"]
    }
    assert vyper4_settings[v4_version_used]["gas%shanghai"]["evmVersion"] == "shanghai"
