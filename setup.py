#!/usr/bin/env python
from setuptools import find_packages, setup

extras_require = {
    "test": [  # `test` GitHub Action jobs uses this
        "pytest>=6.0",  # Core testing package
        "pytest-mock",
        "pytest-xdist",  # multi-process runner
        "pytest-cov",  # Coverage analyzer plugin
        "hypothesis>=6.2.0,<7.0",  # Strategy-based fuzzer
        "snekmate",  # Python package-sources integration testing
    ],
    "lint": [
        "black>=24.10.0,<25",  # Auto-formatter and linter
        "mypy>=1.13.0,<2",  # Static type analyzer
        "types-setuptools",  # Needed due to mypy typeshed
        "flake8>=7.1.1,<8",  # Style linter
        "flake8-pydantic",  # For detecting issues with Pydantic models
        "flake8-type-checking",  # Detect imports to move in/out of type-checking blocks
        "isort>=5.13.2",  # Import sorting linter
        "mdformat>=0.7.18",  # Auto-formatter for markdown
        "mdformat-gfm>=0.3.5",  # Needed for formatting GitHub-flavored markdown
        "mdformat-frontmatter>=0.4.1",  # Needed for frontmatters-style headers in issue templates
        "mdformat-pyproject>=0.0.1",  # Allows configuring in pyproject.toml
    ],
    "release": [  # `release` GitHub Action job uses this
        "setuptools",  # Installation tool
        "wheel",  # Packaging tool
        "twine",  # Package upload tool
    ],
    "dev": [
        "commitizen",  # Manage commits and publishing releases
        "pre-commit",  # Ensure that linters are run prior to committing
        "pytest-watch",  # `ptw` test watcher/runner
        "IPython",  # Console for interacting
        "ipdb",  # Debugger (Must use `export PYTHONBREAKPOINT=ipdb.set_trace`)
    ],
}

# NOTE: `pip install -e .[dev]` to install package
extras_require["dev"] = (
    extras_require["test"]
    + extras_require["lint"]
    + extras_require["release"]
    + extras_require["dev"]
)

with open("./README.md") as readme:
    long_description = readme.read()


setup(
    name="ape-vyper",
    use_scm_version=True,
    setup_requires=["setuptools_scm"],
    description="Plugin for Ape Ethereum Framework for compiling Vyper contracts",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="ApeWorX Ltd.",
    author_email="admin@apeworx.io",
    url="https://github.com/ApeWorX/ape-vyper",
    include_package_data=True,
    install_requires=[
        "eth-ape>=0.8.15,<0.9",
        "ethpm-types",  # Use same version as eth-ape
        "tqdm",  # Use same version as eth-ape
        "vvm>=0.2.0,<0.3",
        "vyper>=0.3.7,<0.5",
    ],
    python_requires=">=3.10,<4",
    extras_require=extras_require,
    py_modules=["ape_vyper"],
    entry_points={
        "ape_cli_subcommands": [
            "ape_vyper=ape_vyper._cli:cli",
        ],
    },
    license="Apache-2.0",
    zip_safe=False,
    keywords="ethereum",
    packages=find_packages(exclude=["tests", "tests.*"]),
    package_data={"ape_vyper": ["py.typed"]},
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Natural Language :: English",
        "Operating System :: MacOS",
        "Operating System :: POSIX",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
)
