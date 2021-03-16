import os
from typing import Dict
from pathlib import Path

from ape.plugins.compiler_api import CompilerAPI
from ape.ethpm import PackageManifest, ContractType, Bytecode

import click
import vvm

import json


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
            return

        for (name, source) in pkg_manifest.sources.items():
            if source.type != "vyper":
                continue

            if not source.content:
                # if error, e.g. no URL, error to console, let other 'good' contracts finish - don't halt entire process
                # TODO checksum checking
                source.load_content()

            result = vvm.compile_source(source.content)

            # o = source.urls[0].split("/")[-1]
            # temp = f"/home/shade/source/ape-vyper/{o}"
            # with open(temp, "w") as fp:
            #     json.dump(result, fp, indent=4)

            result = result["<stdin>"]
            db = Bytecode(result["bytecode"], None, None)
            rb = Bytecode(result["bytecode_runtime"], None, None)
            abi = result["abi"]
            userdoc = result["userdoc"]
            devdoc = result["devdoc"]
            contract = ContractType(name, name, db, rb, abi, userdoc, devdoc)

            if not pkg_manifest.contractTypes:
                pkg_manifest.contractTypes = []

            pkg_manifest.contractTypes.append(contract)

        click.echo("vyper compilation finished")
