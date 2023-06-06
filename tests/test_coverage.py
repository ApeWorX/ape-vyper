import re
import shutil
import tempfile
from pathlib import Path
from typing import List

import pytest

EXPECTED_COVERAGE_REPORT = r"""
\s*=+ Coverage Profile =+\s*
\s*Contract Coverage\s*
\s*
\s*Name\s+Stmts\s+Miss\s+Cover\s+Funcs\s*
\s*─+\s*
\s*coverage_test\.vy\s+4\s+0\s+100.0%\s+100.0%\s*
""".lstrip()
COVERAGE_START_PATTERN = re.compile(r"=+ Coverage Profile =+")


@pytest.fixture
def coverage_project_path(projects_path):
    return projects_path / "coverage_project"


@pytest.fixture
def coverage_project(config, coverage_project_path):
    build_dir = coverage_project_path / ".build"
    shutil.rmtree(build_dir, ignore_errors=True)
    with tempfile.TemporaryDirectory() as base_dir:
        # Copy Coverage project
        project_dir = Path(base_dir) / "coverage_project"
        shutil.copytree(coverage_project_path, project_dir)
        with config.using_project(project_dir) as project:
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
    actual = _get_coverage_report(result.outlines)
    expected = [x.strip() for x in EXPECTED_COVERAGE_REPORT.split("\n")]
    _assert_coverage(actual, expected)


def _get_coverage_report(lines: List[str]) -> List[str]:
    ret = []
    started = False
    for line in lines:
        if not started:
            if COVERAGE_START_PATTERN.match(line):
                # Started.
                started = True
                ret.append(line.strip())
            else:
                # Wait for start.
                continue

        elif started and re.match(r"=+ .* =+", line):
            # Ended.
            ret.append(line.strip())
            return ret

        else:
            # Line in between start and end.
            ret.append(line.strip())

    return ret


def _assert_coverage(actual: List[str], expected: List[str]):
    for idx, (a_line, e_line) in enumerate(zip(actual, expected)):
        message = f"Failed at index {idx}. Expected={e_line}, Actual={a_line}"
        assert re.match(e_line, a_line), message
