from ape.utils import Abort
import pytest

# def test_integration(passing_project):
#     assert "contract" in passing_project
#     assert "contract_no_pragma" in project.contracts.passing_contracts


def test_failure(failing_project):
    with pytest.raises(Abort):
        assert "contract_with_error" in failing_project
