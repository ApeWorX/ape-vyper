from pathlib import Path
from typing import TYPE_CHECKING

from ape.logging import logger
from ape.utils.os import clean_path

if TYPE_CHECKING:
    from packaging.version import Version


def output_details(*source_ids: str, version: "Version"):
    source_ids = "\n\t".join(sorted([clean_path(Path(x)) for x in source_ids]))
    log_str = f"Compiling using Vyper compiler '{version}'.\nInput:\n\t{source_ids}"
    logger.info(log_str)
