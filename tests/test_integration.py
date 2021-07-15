from pathlib import Path

import pytest  # type: ignore

PASSING_CONTRACTS_FOLDER = Path(__file__).parent / "contracts" / "passing_contracts"
FAILING_CONTRACTS_FOLDER = Path(__file__).parent / "contracts" / "failing_contracts"


@pytest.mark.parametrize("path", PASSING_CONTRACTS_FOLDER.glob("*.vy"))
def test_pass(path, compiler):
    assert compiler.compile([path])


@pytest.mark.parametrize("path", FAILING_CONTRACTS_FOLDER.glob("*.vy"))
def test_failure(path, compiler):
    with pytest.raises(Exception):
        compiler.compile([path])
