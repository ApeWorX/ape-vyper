# Quick Start

Ape compiler plugin around [VVM](https://github.com/vyperlang/vvm)

## Dependencies

- [python3](https://www.python.org/downloads) version 3.10 up to 3.12.

## Installation

### via `pip`

You can install the latest release via [`pip`](https://pypi.org/project/pip/):

```bash
pip install ape-vyper
```

### via `setuptools`

You can clone the repository and use [`setuptools`](https://github.com/pypa/setuptools) for the most up-to-date version:

```bash
git clone https://github.com/ApeWorX/ape-vyper.git
cd ape-vyper
python3 setup.py install
```

## Quick Usage

First, place Vyper contract source files (files with extension `.vy`) in your Ape project's contracts folder.
An example Vyper contract can be found [here](https://vyper.readthedocs.io/en/stable/vyper-by-example.html).
Then, from your root Ape project folder, run the command:

```bash
ape compile
```

The `.vy` files in your project will compile into `ContractTypes` that you can deploy and interact with in Ape.

### Contract Flattening

For ease of publishing, validation, and some other cases it's sometimes useful to "flatten" your contract into a single file.
This combines your contract and any imported interfaces together in a way the compiler can understand.
You can do so with a command like this:

```bash
ape vyper flatten contracts/MyContract.vy build/MyContractFlattened.vy
```

> [!WARNING]
> This feature is experimental. Please [report any bugs](https://github.com/ApeWorX/ape-solidity/issues/new?assignees=&labels=bug&projects=&template=bug.md) you find when trying it out.

### Compiler Version

By default, the `ape-vyper` plugin uses version pragma for version specification.
However, you can also configure the version directly in your `pyproject.toml` file:

```toml
[tool.vyper.version]
version = "0.3.7"
```

### EVM Versioning

By default, `ape-vyper` will use whatever version of EVM rules are set as default in the compiler version that gets used,
or based on what the `#pragma evm-version ...` pragma comment specifies (available post-`v0.3.10`).
Sometimes, you might want to use a different version, such as deploying on Arbitrum or Optimism where new opcodes are not supported yet.
If you want to require a different version of EVM rules to use in the configuration of the compiler, set it in your `ape-config.yaml` like this:

```toml
[tool.ape.vyper]
evm_version = "paris"
```

**NOTE**: The config value chosen will not override if a pragma is set in a contract.

### Interfaces

You can not compile interface source files directly.
Thus, you must place interface files in a directory named `interfaces` in your `contracts_folder` e.g. `contracts/interfaces/IFace.vy`.
Then, these files can be imported in other `.vy` sources files via:

```python
import interfaces.IFace as IFace
```

Alternatively, use JSON interfaces from dependency contract types by listing them under the `import_remapping` key:

```toml
[[tool.ape.dependencies]]
name = "VyperVoting"
github = "vyperlang/vyper"
contracts_folder = "examples/voting/"
version = "v0.3.8"

[tool.ape.vyper]
import_remapping = ["voting=VyperVoting@v0.3.8"]
```

Import the voting contract types like this:

```python
# @version 0.3.10

import voting.ballot as ballot
```

### Decimals

To use decimals on Vyper 0.4, use the following config:

```toml
[tool.ape.vyper]
enable_decimals = true
```

### Pragmas

Ape-Vyper supports Vyper 0.3.10's [new pragma formats](https://github.com/vyperlang/vyper/pull/3493)

#### Version Pragma

```python
#pragma version 0.3.10
```

#### EVM Version Pragma

```python
#pragma evm-version paris
```

#### Optimization Pragma

```python
#pragma optimize codesize
```

### VVM CLI

You can install versions of Vyper using the `ape vyper vvm` CLI tools.
List installed versions using:

```shell
ape vyper vvm list
```

To list the available Vyper versions, do:

```shell
ape vyper vvm list --available
```

Install more versions using the command:

```shell
ape vyper vvm install 0.3.7 0.3.10
```

### Custom Output Format

To customize Vyper's output format (like the native `-f` flag), you can configure the output format:
For example, to only get the ABI, do:

```toml
[tool.ape.vyper]
output_format = ["abi"]
```

To do this using the CLI only (adhoc), use the following command:

```shell
ape compile --config-override '{"vyper": {"output_format": ["abi"]}}'
```

#### Solc JSON Format

`ape-vyper` supports the `socl_json` format.
To use this format, configure `ape-vyper` like:

```toml
[tool.ape.vyper]
output_format = ["solc_json"]
```

**Note**: Normally, in Vyper, you cannot use `solc_json` with other formats.
However, `ape-vyper` handles this by running separately for the `solc_json` request.

Be sure to use the `--force` flag when compiling to ensure you get the solc JSON output.

```shell
ape compile file_needing_solc_json_format.vy -f
```

To get a dependency source file in this format, configure and compile the dependency.

```toml
[[tool.ape.dependencies]]
name = "my_dep"
config_override = { "vyper" = { "output_format" = ["solc_json"] } }
```

And then run:

```shell
ape pm compile --force
```
