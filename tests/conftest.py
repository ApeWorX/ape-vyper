from pathlib import Path

import pytest
from ape import Project


@pytest.fixture
def project():
    return Project(Path(__file__).parent)
