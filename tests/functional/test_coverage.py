import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

import pytest
from ape import Project
from ape.utils import create_tempdir

LINES_VALID = 8
MISSES = 0
LINE_COV = "100.0".replace(".", r"\.")
FUNC_COV = "100.0".replace(".", r"\.")

EXPECTED_COVERAGE_REPORT = r"""
\s*=+\s*Coverage Profile\s*=+\s*
\s*\s*coverage_test Coverage\s*\s*
\s*
\s*Func\s+Stmts\s+Miss\s+Cover\s*
\s*─+\s*
\s*__cannot_send_ether_to_nonpayable_function__\s+1\s+0\s+100\.0%\s*
\s*__fallback_not_defined__\s+1\s+0\s+100\.0%\s*
\s*_immutable_number\s+0\s+0\s+100\.0%\s*
\s*_number\s+0\s+0\s+100\.0%\s*
\s*foo_method\(\)\s+1\s+0\s+100\.0%\s*
\s*foo_method\(uint256\)\s+1\s+0\s+100\.0%\s*
\s*foo_method\(uint256,uint256\)\s+3\s+0\s+100\.0%\s*
\s*view_method\s+1\s+0\s+100\.0%\s*
\s*
\s*line=100\.0%, func=100\.0%\s*
\s*
\s*exclude_part_of_contract Coverage\s*
\s*
\s*Func\s+Stmts\s+Miss\s+Cover\s*
\s*─+\s*
\s*__fallback_not_defined__\s+1\s+1\s+0\.0%\s*
\s*include_me\s+1\s+1\s+0\.0%\s*
\s*
\s*line=0\.0%, func=0\.0%\s*
""".lstrip()
COVERAGE_START_PATTERN = re.compile(r"=+ Coverage Profile =+")


@pytest.fixture
def coverage_project_path(projects_path):
    return projects_path / "coverage_project"


@pytest.fixture
def coverage_project(config, coverage_project_path):
    build_dir = coverage_project_path / ".build"
    shutil.rmtree(build_dir, ignore_errors=True)

    with create_tempdir() as temp_dir:
        shutil.copytree(coverage_project_path, temp_dir, dirs_exist_ok=True)
        yield Project(temp_dir)

    shutil.rmtree(build_dir, ignore_errors=True)


@pytest.fixture
def setup_pytester(pytester, coverage_project):
    tests_path = coverage_project.tests_folder

    # Make other files
    def _make_all_files(base: Path, prefix: Optional[Path] = None):
        if not base.is_dir():
            return

        for file in base.iterdir():
            if file.is_dir() and not file.name == "tests":
                _make_all_files(file, prefix=Path(file.name))
            elif file.is_file():
                name = (prefix / file.name).as_posix() if prefix else file.name
                name_to_make = name

                if name == "pyproject.toml":
                    # Hack in in-memory overrides for testing purposes.
                    text = str(coverage_project.config)
                    suffix = ".yaml"
                    name_to_make = "ape-config.yaml"
                else:
                    text = file.read_text(encoding="utf8")
                    suffix = file.suffix

                src = {name_to_make: text.splitlines()}
                pytester.makefile(suffix, **src)

    # Assume all tests should pass
    num_passes = 0
    num_failed = 0
    test_files = {}
    for file_path in tests_path.iterdir():
        if file_path.name.startswith("test_") and file_path.suffix == ".py":
            content = file_path.read_text(encoding="utf8")
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
    _make_all_files(coverage_project.path)

    # Check for a conftest.py
    conftest = tests_path / "conftest.py"
    if conftest.is_file():
        pytester.makeconftest(conftest.read_text(encoding="utf8"))

    # Returns expected number of passing tests.
    return num_passes, num_failed


def test_coverage(geth_provider, setup_pytester, coverage_project, pytester):
    passed, failed = setup_pytester
    result = pytester.runpytest_subprocess("--coverage")
    try:
        result.assert_outcomes(passed=passed, failed=failed)
    except ValueError:
        pytest.fail(str(result.stderr))

    actual = _get_coverage_report(result.outlines)
    expected = [x.strip() for x in EXPECTED_COVERAGE_REPORT.split("\n")]
    _assert_coverage(actual, expected)

    # Ensure XML was created.
    base_dir = pytester.path / ".build"
    xml_path = base_dir / "coverage.xml"
    _assert_xml(xml_path)
    html_path = base_dir / "htmlcov"
    assert html_path.is_dir()
    index = html_path / "index.html"
    assert index.is_file()
    _assert_html(index)


def _get_coverage_report(lines: list[str]) -> list[str]:
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


def _assert_coverage(actual: list[str], expected: list[str]):
    for idx, (a_line, e_line) in enumerate(zip(actual, expected)):
        message = f"Failed at index {idx}. Expected={e_line}, Actual={a_line}"
        assert re.match(e_line, a_line), message


def _assert_xml(xml_path: Path):
    assert xml_path.is_file()
    xml = xml_path.read_text(encoding="utf8")
    assert '<?xml version="1.0" ?>' in xml

    # Show is valid XML.
    tree = ET.parse(str(xml_path))

    # Assert there are sources.
    sources = tree.find("sources")
    assert sources is not None
    source = sources.find("source")
    assert source is not None
    assert source.text is not None
    assert "contracts" in source.text

    # Assert there are statements.
    packages = tree.find("packages")
    assert packages is not None
    package = packages.find("package")
    assert package is not None
    classes = package.find("classes")
    assert classes is not None
    _class = classes.find("class")
    assert _class is not None
    lines = _class.find("lines")
    assert lines is not None
    line = lines.find("line")
    assert line is not None
    assert "hits" in line.keys()
    assert "number" in line.keys()


def _assert_html(index_html: Path):
    html = index_html.read_text(encoding="utf8")
    assert html.startswith("<!DOCTYPE html>")
    assert "Generated by Ape Framework" in html
    expected_columns = (
        "Source",
        "Statements",
        "Missing",
        "Statement Coverage",
        "Function Coverage",
    )
    for idx, column in enumerate(expected_columns):
        col_no = idx + 1
        expected_col_tag = f'<th class="column{col_no}">{column}</th>'
        assert expected_col_tag in html

    assert "100.0%</td>" in html
