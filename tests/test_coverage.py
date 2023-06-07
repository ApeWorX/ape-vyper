import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List

import pytest

LINES_VALID = 7
MISSES = 0
LINE_COV = "100.0".replace(".", r"\.")
FUNC_COV = "100.0".replace(".", r"\.")
EXPECTED_COVERAGE_REPORT = rf"""
\s*=+ Coverage Profile =+\s*
\s*Contract Coverage\s*
\s*
\s*Name\s+Stmts\s+Miss\s+Cover\s+Funcs\s*
\s*─+\s*
\s*coverage_test\.vy\s+{LINES_VALID}\s+{MISSES}\s+{LINE_COV}%\s+{FUNC_COV}%\s*
\s*exclude_part_of_contract\.vy\s+\d\s+\d\s*\d\.\d%\s+\d\.\d%\s*
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

    # Ensure XML was created.
    base_dir = coverage_project.local_project._cache_folder
    xml_path = base_dir / "coverage.xml"
    _assert_xml(xml_path)
    html_path = base_dir / "htmlcov"
    assert html_path.is_dir()
    index = html_path / "index.html"
    assert (html_path / "favicon.ico").is_file()
    assert index.is_file()
    _assert_html(index)


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


def _assert_xml(xml_path: Path):
    assert xml_path.is_file()
    xml = xml_path.read_text()
    assert '<?xml version="1.0" ?>' in xml
    # Since is a parametrized function, the full selectors should be present.
    assert "foo_method()" in xml
    assert "foo_method(uint256)" in xml
    assert "foo_method(uint256,uint256)" in xml
    # Show is valid XML.
    tree = ET.parse(str(xml_path))
    projects = tree.find("projects")
    assert projects is not None
    project = projects.find("project")
    assert project is not None
    sources = project.find("sources")
    assert sources is not None
    src = sources.find("source")
    assert src is not None
    contracts = src.find("contracts")
    assert contracts is not None
    contract = contracts.find("contract")
    assert contract is not None
    functions = contract.find("functions")
    assert functions is not None
    function = functions.find("function")
    assert function is not None
    statements = function.find("statements")
    assert statements is not None
    statement = statements.find("statement")
    assert statement is not None
    assert "hits" in statement.keys()
    assert "pcs" in statement.keys()


def _assert_html(index_html: Path):
    html = index_html.read_text()
    assert html.startswith("<!DOCTYPE html>")
    assert "Generated by Ape Framework" in html
    expected_columns = (
        "Source",
        "Statements",
        "Missing",
        "Statement Coverage",
        "Function Coverage",
    )
    for column in expected_columns:
        expected_col_tag = f"<th>{column}</th>"
        assert expected_col_tag in html

    assert "<td>100.0%</td>" in html
