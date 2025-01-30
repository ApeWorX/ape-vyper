import re
from pathlib import Path
from typing import TYPE_CHECKING

from ape.logging import logger
from ape.utils.os import clean_path

from ape_vyper._utils import DEV_MSG_PATTERN

if TYPE_CHECKING:
    from packaging.version import Version


def output_details(*source_ids: str, version: "Version"):
    source_ids_str = "\n\t".join(sorted([clean_path(Path(x)) for x in source_ids]))
    log_str = f"Compiling using Vyper compiler '{version}'.\nInput:\n\t{source_ids_str}"
    logger.info(log_str)


def map_dev_messages(content: dict) -> dict:
    dev_messages = {}
    for line_no, line in content.items():
        if match := re.search(DEV_MSG_PATTERN, line):
            dev_messages[line_no] = match.group(1).strip()

    return dev_messages
