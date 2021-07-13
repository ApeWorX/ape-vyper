from ape.utils import Abort

# def test_integration(project):
#     assert "contract" in project.contracts.passing_contracts
#     assert "contract_no_pragma" in project.contracts.passing_contracts


def test_failure(project):
    try:
        assert "contract_with_error" in project.contracts.erroring_contracts
    except Abort:
        pass
