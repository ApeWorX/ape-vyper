# Development

To get started with working on the codebase, use the following steps prepare your local environment:

```bash
# clone the github repo and navigate into the folder
git clone https://github.com/ApeWorX/ape-vyper.git
cd ape-vyper

# create and load a virtual environment
python3 -m venv venv
source venv/bin/activate

# install ape-vyper into the virtual environment
python setup.py install

# install the developer dependencies (-e is interactive mode)
pip install -e .'[dev]'
```

## Pre-Commit Hooks

We use [`pre-commit`](https://pre-commit.com/) hooks to simplify linting and ensure consistent formatting among contributors.
Use of `pre-commit` is not a requirement, but is highly recommended.

Install `pre-commit` locally from the root folder:

```bash
pip install pre-commit
pre-commit install
```

Committing will now automatically run the local hooks and ensure that your commit passes all lint checks.

## Pull Requests

Pull requests are welcomed! Please adhere to the following:

- Ensure your pull request passes our linting checks
- Include test cases for any new functionality
- Include any relevant documentation updates

It's a good idea to make pull requests early on.
A pull request represents the start of a discussion, and doesn't necessarily need to be the final, finished submission.

If you are opening a work-in-progress pull request to verify that it passes CI tests, please consider
[marking it as a draft](https://help.github.com/en/github/collaborating-with-issues-and-pull-requests/about-pull-requests#draft-pull-requests).

Join the Ethereum Python [Discord](https://discord.gg/PcEJ54yX) if you have any questions.

## Testing

By default, the test suite will use a new, temporary path for the Vyper compiler installations.
This ensures that the tests always run from a clean slate without any relying on existing installations.

If you wish to use your existing `~/.vvm` installations instead, you must set the environment variable `APE_VYPER_USE_SYSTEM_VYPER=1`.

This will ensure that vvm's default path will be used, but any compilers installed as part of the tests will not be removed after tests have completed.
