import os
import shutil
from subprocess import run

import pytest


@pytest.fixture(
    autouse=True, params=("snekmate_from_pypi", "snekmate_from_python", "snekmate_not_configured")
)
def chgproject(projects_path, request):
    path = projects_path / request.param
    here = os.getcwd()
    os.chdir(path)
    shutil.rmtree(path / ".build", ignore_errors=True)
    yield path
    shutil.rmtree(path / ".build", ignore_errors=True)
    os.chdir(here)


def test_snekmate(chgproject):
    result = run(["ape", "compile", "."])
    assert result.returncode == 0
    assert (chgproject / ".build").is_dir()
