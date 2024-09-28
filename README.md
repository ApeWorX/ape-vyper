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

> \[!WARNING\]
> This feature is experimental. Please [report any bugs](https://github.com/ApeWorX/ape-solidity/issues/new?assignees=&labels=bug&projects=&template=bug.md) you find when trying it out.

### Compiler Version

By default, the `ape-vyper` plugin uses version pragma for version specification.
However, you can also configure the version directly in your `ape-config.yaml` file:

```yaml
vyper:
  version: 0.3.7
```

### EVM Versioning

By default, `ape-vyper` will use whatever version of EVM rules are set as default in the compiler version that gets used,
or based on what the `#pragma evm-version ...` pragma comment specifies (available post-`v0.3.10`).
Sometimes, you might want to use a different version, such as deploying on Arbitrum or Optimism where new opcodes are not supported yet.
If you want to require a different version of EVM rules to use in the configuration of the compiler, set it in your `ape-config.yaml` like this:

```yaml
vyper:
  evm_version: paris
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

```yaml
# Use `voting` example contracts from Vyperlang repo.
dependencies:
  - name: VyperVoting
    github: vyperlang/vyper
    contracts_folder: examples/voting/
    version: v0.3.8

# Automatically allow importing voting contracts in your project.
vyper:
  import_remapping:
    - "voting=VyperVoting@v0.3.8"
```

Import the voting contract types like this:

```python
# @version 0.3.10

import voting.ballot as ballot
```

### Decimals

To use decimals on Vyper 0.4, use the following config:

```yaml
vyper:
  enable_decimals: true
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
