import shutil

import pytest


@pytest.fixture
def coverage_project_path(projects_path):
    return projects_path / "coverage_project"


@pytest.fixture
def coverage_project(config, coverage_project_path):
    build_dir = coverage_project_path / ".build"
    shutil.rmtree(build_dir, ignore_errors=True)
    with config.using_project(coverage_project_path) as project:
        yield project

    shutil.rmtree(build_dir, ignore_errors=True)


@pytest.fixture
def setup_pytester(pytester, coverage_project_path):
    tests_path = coverage_project_path / "tests"

    # Assume all tests should pass
    num_passes = 0
    num_failed = 0
    test_files = {}
    for file_path in tests_path.iterdir():
        if file_path.name.startswith("test_") and file_path.suffix == ".py":
            content = file_path.read_text()
            test_files[file_path.name] = content
            num_passes += len(
                [
                    x
                    for x in content.split("\n")
                    if x.startswith("def test_") and not x.startswith("def test_fail_")
                ]
            )
            num_failed += len([x for x in content.split("\n") if x.startswith("def test_fail_")])

    pytester.makepyfile(**test_files)

    # Check for a conftest.py
    conftest = tests_path / "conftest.py"
    if conftest.is_file():
        pytester.makeconftest(conftest.read_text())

    # Returns expected number of passing tests.
    return num_passes, num_failed


def test_coverage(geth_provider, setup_pytester, coverage_project, pytester):
    passed, failed = setup_pytester
    result = pytester.runpytest("--coverage")
    result.assert_outcomes(passed=passed, failed=failed)
