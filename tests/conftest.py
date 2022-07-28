import pytest  # type: ignore

from ape_vyper.compiler import VyperCompiler
import ape
import shutil
from pathlib import Path
from distutils.dir_util import copy_tree


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
    for path in (
        project_path,
        project_path / "contracts/passing_projects"
    ):
        for cache in (path / ".build", path / "contracts" / ".cache"):
            if cache.is_dir():
                shutil.rmtree(cache)

    copy_tree(project_source_dir.as_posix(), project_dest_dir.as_posix())
    with config.using_project(project_dest_dir) as project:
        yield project
        if project._project._cache_folder.is_dir():
            shutil.rmtree(project._project._cache_folder)

