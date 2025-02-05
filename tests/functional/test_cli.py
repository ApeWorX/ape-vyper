import subprocess

import pytest
from ape.utils import create_tempdir
from packaging.version import Version

from ape_vyper._cli import cli


@pytest.mark.parametrize(
    "contract_name,expected",
    [
        # This first one has most known edge cases
        (
            "flatten_me.vy",
            [
                "from vyper.interfaces import ERC20",
                "interface Dep:",
                "interface IFace:",
                "interface IFaceTwo:",
            ],
        ),
    ],
)
def test_flatten(project, contract_name, expected, cli_runner):
    path = project.contracts_folder / contract_name
    arguments = ["flatten", str(path)]
    end = ("--project", str(project.path))
    with create_tempdir() as tmpdir:
        file = tmpdir / "flatten.vy"
        arguments.extend([str(file), *end])
        result = cli_runner.invoke(cli, arguments, catch_exceptions=False)
        assert result.exit_code == 0, result.stderr_bytes
        output = file.read_text(encoding="utf8")
        for expect in expected:
            assert expect in output


def test_compile():
    """
    Integration: Testing the CLI using an actual subprocess because
    it is the only way to test compiling the project such that it
    isn't treated as a tempdir project.
    """
    # Use a couple contracts
    cmd_ls = ("ape", "compile", "subdir", "--force")
    completed_process = subprocess.run(cmd_ls, capture_output=True)
    output = completed_process.stdout.decode(encoding="utf8")
    assert "SUCCESS" in output
    assert "zero_four_in_subdir.vy" in output


def test_vvm_list(mocker, cli_runner):
    mock_installed = mocker.patch("ape_vyper._cli.get_installed_vyper_versions")
    mock_installed.return_value = ["0.3.3"]
    result = cli_runner.invoke(cli, ["vvm", "list"])
    assert result.exit_code == 0
    assert "Installed vyper versions:\n0.3.3" in result.stdout


def test_vvm_list_available(mocker, cli_runner):
    mock_available = mocker.patch("ape_vyper._cli.get_installable_vyper_versions")
    mock_available.return_value = [Version("0.3.3")]
    result = cli_runner.invoke(cli, ["vvm", "list", "--available"])
    assert result.exit_code == 0
    assert "Available vyper versions:\n0.3.3" in result.stdout


def test_vvm_install(cli_runner):
    result = cli_runner.invoke(cli, ["vvm", "install", "0.4.0"])
    assert result.exit_code == 0
