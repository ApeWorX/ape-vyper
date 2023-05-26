import os
import shutil
from contextlib import contextmanager
from distutils.dir_util import copy_tree
from pathlib import Path
from tempfile import mkdtemp

import ape
import pytest
import vvm  # type: ignore

from ape_vyper.compiler import VyperCompiler

# NOTE: Ensure that we don't use local paths for these
DATA_FOLDER = Path(mkdtemp()).resolve()
PROJECT_FOLDER = Path(mkdtemp()).resolve()
ape.config.DATA_FOLDER = DATA_FOLDER
ape.config.PROJECT_FOLDER = PROJECT_FOLDER

# Needed for integration testing
pytest_plugins = ["pytester"]


@contextmanager
def _tmp_vvm_path(monkeypatch):
    vvm_install_path = mkdtemp()

    monkeypatch.setenv(
        vvm.install.VVM_BINARY_PATH_VARIABLE,
        vvm_install_path,
    )

    yield vvm_install_path

    if Path(vvm_install_path).is_dir():
        shutil.rmtree(vvm_install_path, ignore_errors=True)


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


@pytest.fixture
def temp_vvm_path(monkeypatch):
    """
    Creates a new, temporary installation path for vvm for a given test.
    """
    with _tmp_vvm_path(monkeypatch) as path:
        yield path


@pytest.fixture
def data_folder():
    return DATA_FOLDER


@pytest.fixture
def project_folder():
    return PROJECT_FOLDER


@pytest.fixture
def compiler():
    return VyperCompiler()


@pytest.fixture
def config():
    return ape.config


@pytest.fixture(autouse=True)
def project(config):
    project_source_dir = Path(__file__).parent
    project_dest_dir = config.PROJECT_FOLDER / project_source_dir.name

    # Delete build / .cache that may exist pre-copy
    project_path = Path(__file__).parent
    cache = project_path / ".build"

    if cache.is_dir():
        shutil.rmtree(cache)

    copy_tree(project_source_dir.as_posix(), project_dest_dir.as_posix())
    with config.using_project(project_dest_dir) as project:
        yield project
        if project.local_project._cache_folder.is_dir():
            shutil.rmtree(project.local_project._cache_folder)


@pytest.fixture
def geth_provider():
    if not ape.networks.active_provider or ape.networks.provider.name != "geth":
        with ape.networks.ethereum.local.use_provider(
            "geth", provider_settings={"uri": "http://127.0.0.1:5550"}
        ) as provider:
            yield provider
    else:
        yield ape.networks.provider


@pytest.fixture
def account():
    return ape.accounts.test_accounts[0]


@pytest.fixture
def registry(geth_provider, account, project):
    return account.deploy(project.registry)


@pytest.fixture
def contract(registry, account, project):
    return account.deploy(project.traceback_contract, registry)
