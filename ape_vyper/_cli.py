import sys
from pathlib import Path

import ape
import click
from ape.cli.options import ape_cli_context, project_option
from vvm import (  # type: ignore
    get_installable_vyper_versions,
    get_installed_vyper_versions,
    get_vvm_install_folder,
    install_vyper,
)


@click.group
def cli():
    """`vyper` command group"""


@cli.command()
@ape_cli_context()
@project_option()
@click.argument("CONTRACT", type=click.Path(exists=True, resolve_path=True))
@click.argument("OUTFILE", type=click.Path(exists=False, resolve_path=True, writable=True))
def flatten(cli_ctx, project, contract: Path, outfile: Path):
    """Flatten select contract source files into a single file"""

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


@vvm.command(name="list")
@click.option("--available", is_flag=True, help="Show available vyper versions")
def list_installed(available: bool):
    """List vyper installed versions"""

    if available:
        if available_versions := get_installable_vyper_versions():
            # First, show the installed.
            _list_installed()

            # Show available.
            click.echo("\nAvailable vyper versions:")
            for version in available_versions:
                click.echo(f"{version}")

    else:
        _list_installed(allow_pager=True)


def _list_installed(allow_pager: bool = False):
    versions = get_installed_vyper_versions()
    if allow_pager and len(versions) > 10:
        click.echo_via_pager(f"{v}\n" for v in versions)
    else:
        click.echo("Installed vyper versions:")
        for version in versions:
            click.echo(version)


@vvm.command(short_help="Install vyper")
@click.argument("versions", nargs=-1)
@click.option("--vvm-binary-path", help="The path to Vyper binaries")
@click.option("--hide-progress", is_flag=True)
def install(versions, vvm_binary_path, hide_progress):
    """Install Vyper binaries by version"""

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
