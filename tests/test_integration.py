def test_integration(project):
    assert "contract" in project.contracts
    assert "contract_no_pragma" in project.contracts
