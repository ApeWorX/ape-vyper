from pathlib import Path

import ape
import click
from ape.cli.options import ape_cli_context, project_option


@click.group
def cli():
    """`vyper` command group"""


@cli.command(short_help="Flatten select contract source files")
@ape_cli_context()
@project_option()
@click.argument("CONTRACT", type=click.Path(exists=True, resolve_path=True))
@click.argument("OUTFILE", type=click.Path(exists=False, resolve_path=True, writable=True))
def flatten(cli_ctx, project, contract: Path, outfile: Path):
    """
    Flatten a contract into a single file
    """
    with Path(outfile).open("w") as fout:
        content = ape.compilers.vyper.flatten_contract(
            Path(contract),
            base_path=ape.project.contracts_folder,
            project=project,
        )
        fout.write(str(content))
