from pathlib import Path

import pytest  # type: ignore
from ape import Project


@pytest.fixture
def project():
    breakpoint()
    return Project(Path(__file__).parent.passing_contracts)

@pytest.fixture
def failing_project():
    breakpoint()
    return Project(Path(__file__).parent.erroring_projects)
    
