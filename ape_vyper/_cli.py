import sys
from collections.abc import Iterable
from pathlib import Path

import ape
import click
from ape.cli.options import ape_cli_context, project_option
from vvm import get_installed_vyper_versions, install_vyper


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


@cli.command(short_help="Install vyper")
@click.argument("versions", nargs=-1)
@click.option("--list", "do_list", help="List installed Vyper version")
def install(versions, do_list):
    """
    Install Vyper
    """
    if do_list:
        if versions:
            raise ValueError("Can't use `--list` with versions argument.")

        _list_versions()

    else:
        # Install.
        if versions:
            for version in versions:
                get_installed_vyper_versions(version)

        else:
            click.echo("No version given.", err=True)
            sys.exit(1)


def _list_versions():
    for version in get_installed_vyper_versions():
        click.echo(version)


def _install_versions(versions: Iterable[str]):
    for version in versions:
        install_vyper(version)
