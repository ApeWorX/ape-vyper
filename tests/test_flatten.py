from pathlib import Path

from ape_vyper.flatten import flatten

CONTRACTS_PATH = Path(__file__).parent / "contracts" / "flatten_contracts"


def test_non_import_flatten():
    """
    Test that the flatten function works as expected for files w/o imports.
    """
    assert CONTRACTS_PATH.exists()

    file_path = Path(CONTRACTS_PATH / "hello_world.vy")
    flattened = flatten(path=file_path)
    assert flattened == (
        "# @version 0.3.7\n\n"
        "message: public(String[25])\n\n"
        "@external\ndef __init__():\n"
        '    self.message = "Hello World!"\n'
    )
