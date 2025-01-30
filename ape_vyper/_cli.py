import sys
from pathlib import Path

import ape
import click
from ape.cli.options import ape_cli_context, project_option
from vvm import get_installed_vyper_versions, get_vvm_install_folder, install_vyper  # type: ignore


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


@cli.group
def vvm():
    """`vvm` command group"""


@vvm.command("list", short_help="List vyper installed versions")
def _list():
    versions = get_installed_vyper_versions()
    if len(versions) > 10:
        click.echo_via_pager(versions)
    else:
        for version in get_installed_vyper_versions():
            click.echo(version)


@vvm.command(short_help="Install vyper")
@click.argument("versions", nargs=-1)
@click.option("--vvm-binary-path", help="The path to Vyper binaries")
@click.option("--hide-progress", is_flag=True)
def install(versions, vvm_binary_path, hide_progress):
    """
    Install Vyper
    """
    if versions:
        for version in versions:
            base_path = get_vvm_install_folder(vvm_binary_path=vvm_binary_path)
            if (base_path / f"vyper-{version}").exists():
                click.echo(f"Vyper version '{version}' already installed.")
                continue

            click.echo(f"Installing Vyper '{version}'.")
            install_vyper(version, show_progress=not hide_progress, vvm_binary_path=vvm_binary_path)

    else:
        click.echo("No version given.", err=True)
        sys.exit(1)
