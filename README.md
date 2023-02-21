# Quick Start

Ape compiler plugin around [VVM](https://github.com/vyperlang/vvm)

## Dependencies

- [python3](https://www.python.org/downloads) version 3.8 or greater, python3-dev

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
    version: v0.3.7

# Automatically allow importing voting contracts in your project.
vyper:
  import_remapping:
    - "voting=VyperVoting@v0.3.7"
```

Import the voting contract types like this:

```python
# @version 0.3.7

import voting.ballot as ballot
```
