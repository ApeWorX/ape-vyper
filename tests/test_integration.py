def test_integration(project):
    assert "medcontract" in project.contracts
    assert "contract" in project.contracts
    assert "solcontract" not in project.contracts
