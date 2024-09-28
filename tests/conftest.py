import os
from contextlib import contextmanager
from pathlib import Path

import ape
import pytest
import vvm  # type: ignore
from ape.contracts import ContractContainer, ContractInstance
from ape.utils import create_tempdir
from click.testing import CliRunner

BASE_CONTRACTS_PATH = Path(__file__).parent / "contracts"
TEMPLATES_PATH = BASE_CONTRACTS_PATH / "templates"
FAILING_BASE = BASE_CONTRACTS_PATH / "failing_contracts"
PASSING_BASE = BASE_CONTRACTS_PATH / "passing_contracts"
ALL_VERSIONS = (
    "0.2.1",
    "0.2.2",
    "0.2.3",
    "0.2.15",
    "0.2.16",
    "0.3.0",
    "0.3.1",
    "0.3.2",
    "0.3.3",
    "0.3.4",
    "0.3.7",
    "0.3.9",
    "0.3.10",
    "0.4.0",
)

CONTRACT_VERSION_GEN_MAP = {
    "": (
        "0.3.7",
        "0.3.9",
        "0.3.10",
    ),
    "sub_reverts": [v for v in ALL_VERSIONS if "0.4.0" not in v],
}


@pytest.fixture(scope="session", autouse=True)
def config():
    with ape.config.isolate_data_folder():
        yield ape.config


def contract_test_cases(passing: bool) -> list[str]:
    """
    Returns test-case names for outputting nicely with pytest.
    """
    suffix = "passing_contracts" if passing else "failing_contracts"
    return [p.name for p in (BASE_CONTRACTS_PATH / suffix).glob("*.vy") if p.is_file()]


PASSING_CONTRACT_NAMES = contract_test_cases(True)
FAILING_CONTRACT_NAMES = contract_test_cases(False)
TEMPLATES = [p.stem for p in TEMPLATES_PATH.glob("*.template") if p.is_file()]


# Needed for integration testing
pytest_plugins = ["pytester"]


@contextmanager
def _tmp_vvm_path(monkeypatch):
    with create_tempdir() as path:
        monkeypatch.setenv(
            vvm.install.VVM_BINARY_PATH_VARIABLE,
            f"{path}",
        )
        yield path


@pytest.fixture(
    scope="session",
    autouse=os.environ.get("APE_VYPER_USE_SYSTEM_VYPER") is None,
)
def setup_session_vvm_path(request):
    """
    Creates a new, temporary installation path for vvm when the test suite is
    run.

    This ensures the Vyper installations do not conflict with the user's
    installed versions and that the installations from the tests are cleaned up
    after the suite is finished.
    """
    from _pytest.monkeypatch import MonkeyPatch

    patch = MonkeyPatch()
    request.addfinalizer(patch.undo)

    with _tmp_vvm_path(patch) as path:
        yield path


@pytest.fixture(scope="session", autouse=True)
def generate_contracts():
    """
    Generate contracts from templates. This is used in
    multi-version testing.
    """
    for file in TEMPLATES_PATH.iterdir():
        if not file.is_file() or file.suffix != ".template":
            continue

        versions = CONTRACT_VERSION_GEN_MAP.get(file.stem, CONTRACT_VERSION_GEN_MAP[""])
        for version in versions:
            new_file = PASSING_BASE / f"{file.stem}_{version.replace('.', '')}.vy"
            new_file.unlink(missing_ok=True)
            new_file.write_text(
                file.read_text(encoding="utf8").replace("{{VYPER_VERSION}}", version)
            )


@pytest.fixture
def temp_vvm_path(monkeypatch):
    """
    Creates a new, temporary installation path for vvm for a given test.
    """
    with _tmp_vvm_path(monkeypatch) as path:
        yield path


@pytest.fixture
def compiler_manager():
    return ape.compilers


@pytest.fixture
def compiler(compiler_manager):
    return compiler_manager.vyper


@pytest.fixture(scope="session", autouse=True)
def project(config):
    return config.local_project


@pytest.fixture
def geth_provider():
    if not ape.networks.active_provider or ape.networks.provider.name != "node":
        with ape.networks.ethereum.local.use_provider(
            "node", provider_settings={"uri": "http://127.0.0.1:5550"}
        ) as provider:
            yield provider
    else:
        yield ape.networks.provider


@pytest.fixture
def projects_path():
    return Path(__file__).parent / "projects"


@pytest.fixture
def account():
    return ape.accounts.test_accounts[0]


@pytest.fixture(params=("037", "039", "0310"))
def traceback_contract(request, account, project, geth_provider):
    return _get_tb_contract(request.param, project, account)


@pytest.fixture
def traceback_contract_037(account, project, geth_provider):
    return _get_tb_contract("037", project, account)


@pytest.fixture
def traceback_contract_039(account, project, geth_provider):
    return _get_tb_contract("039", project, account)


@pytest.fixture
def all_versions():
    return ALL_VERSIONS


@pytest.fixture
def cli_runner():
    return CliRunner()


def _get_tb_contract(version: str, project, account) -> ContractInstance:
    project.load_contracts()

    registry_type = project.get_contract(f"registry_{version}")
    assert isinstance(registry_type, ContractContainer), "Setup failed - couldn't get container"
    registry = account.deploy(registry_type)
    contract = project.get_contract(f"traceback_contract_{version}")
    assert isinstance(contract, ContractContainer), "Setup failed - couldn't get container"
    return account.deploy(contract, registry)
