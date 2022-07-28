import shutil
from contextlib import contextmanager
from pathlib import Path
from tempfile import mkdtemp

import pytest  # type: ignore
import vvm  # type: ignore

from ape_vyper.compiler import VyperCompiler


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


@pytest.fixture(scope="session", autouse=True)
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
def compiler():
    return VyperCompiler()
