def test_integration(project):
    assert "contract" in project.contracts
    assert "contract_no_pragma" in project.contracts

def test_failure(project):
    try:
        assert "contract_with_error" in project.contracts
    except VyperError:
        pass

