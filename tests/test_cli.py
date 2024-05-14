import pytest
from ape.utils import create_tempdir

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
def test_cli_flatten(project, contract_name, expected, cli_runner):
    path = project.contracts_folder / contract_name
    with create_tempdir() as tmpdir:
        file = tmpdir / "flatten.vy"
        result = cli_runner.invoke(cli, ("flatten", str(path), str(file)), catch_exceptions=False)
        assert result.exit_code == 0, result.stderr_bytes
        output = file.read_text()
        for expect in expected:
            assert expect in output
