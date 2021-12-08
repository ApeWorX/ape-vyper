# Ape Vyper

Ape compiler plugin around [VVM](https://github.com/vyperlang/vvm)

## Dependencies

* [python3](https://www.python.org/downloads) version 3.7 or greater, python3-dev

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

In your project, make sure you have a `contracts/` directory containing Vyper files (`.vy`).

Then, while this plugin is installed, compile your contracts:

```bash
ape compile
```

The byte-code and ABI for your contracts should now exist in a `__local__.json` file in a `.build/` directory.

## Development

This project is in early development and should be considered an alpha.
Things might not work, breaking changes are likely.
Comments, questions, criticisms and pull requests are welcomed.

## License

This project is licensed under the [Apache 2.0](LICENSE).
