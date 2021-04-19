import click
import vvm
from ape.api.compiler import CompilerAPI
from ape.package import Bytecode, ContractType, PackageManifest


class VyperCompiler(CompilerAPI):
    @property
    def name(self) -> str:
        return "vyper"

    @classmethod
    def handles(self, contract_type: str) -> bool:
        return contract_type == "vy" or contract_type == ".vy" or contract_type == "vyper"

    def compile(self, pkg_manifest: PackageManifest) -> PackageManifest:
        if pkg_manifest.name:
            click.echo(f"compiling {pkg_manifest.name} vyper contracts")
        else:
            click.echo("compiling vyper contracts")

        if not pkg_manifest.sources:
            click.echo("nothing to compile")
            return pkg_manifest

        # TODO: explore async loop here
        for (name, source) in pkg_manifest.sources.items():
            if source.type != "vyper":
                continue

            if not source.content:
                # TODO if error, e.g. no URL, error to console, let other
                # 'good' contracts finish - don't halt entire process
                # TODO checksum checking
                source.load_content()

            result = vvm.compile_source(source.content)

            result = result["<stdin>"]
            db = Bytecode(result["bytecode"], None, None)
            rb = Bytecode(result["bytecode_runtime"], None, None)
            abi = result["abi"]
            userdoc = result["userdoc"]
            devdoc = result["devdoc"]
            contract = ContractType(name, name, db, rb, abi, userdoc, devdoc)

            # TODO: avoid this via default arg w/ dataclass
            if not pkg_manifest.contractTypes:
                pkg_manifest.contractTypes = []

            pkg_manifest.contractTypes.append(contract)

        click.echo("vyper compilation finished")
        return pkg_manifest
