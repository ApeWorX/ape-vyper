from typing import Optional


def flatten(path: str, base_path: Optional[str] = None) -> str:
    """
    Function for flattening a Vyper file from a given path to a string.

    Args:
        path (str): Path to the Vyper file
        base_path (Optional[str]): Base path to the Vyper file

    Returns:
        str: the flattened Vyper file
    """
    flattened = ""

    with open(path, "r") as file:
        for line in file.readlines():
            if "import" in line:
                if base_path is None:
                    raise ValueError(
                        "Base path must be provided when flattening a file with imports."
                    )
                # Parse the import path from the Vyper File
                import_path = base_path + _get_import_path(line)
                flattened += flatten(import_path, base_path)
            else:
                flattened += line

    return flattened


def _get_import_path(line: str) -> str:
    """
    Function for getting the import path from a given import line.

    Args:
        line (str): The import line

    Returns:
        str: The import path
    """
    # TODO: Vyper specific import path parsing
    import_path = ""

    return import_path
