from collections import defaultdict
from collections.abc import Iterator
from enum import Enum
from typing import TYPE_CHECKING, Any

import pytest
from _pytest.fixtures import TopRequest
from ape.utils import ManagerAccessMixin, cached_property
from ethpm_types import ABI
from ethpm_types.abi import ABIType

if TYPE_CHECKING:
    from ape.api import CompilerAPI, TestAccountAPI
    from ape.contracts import ContractInstance, ContractMethodHandler
    from ethpm_types import ContractType


# TODO: Configure EVM context? Pre-compiles? Foundry-like cheatcodes?


class TestModifier(str, Enum):
    # Test result checking modifiers
    CHECK_REVERTS = "check:reverts"
    CHECK_EMITS = "check:emits"

    # Test harness setup modifiers
    MARK_PARAMETRIZE = "mark:parametrize"
    MARK_XFAIL = "mark:xfail"
    # TODO: Support others?

    @classmethod
    def parse_natspec(cls, natspec: str) -> "TestModifier | None":
        """Check if a test modifier exists for the given natspec string"""

        if not natspec.startswith("@custom:test:"):
            return None

        raw_args = natspec.split(" ")
        modifier_type = raw_args[0].replace("@custom:test:", "")

        try:
            obj = cls(modifier_type)

        except ValueError:
            # NOTE: Ignore unsupported `@custom:test` modifiers
            return None

        obj._raw_args = " ".join(raw_args[1:])
        return obj

    @property
    def args(self) -> list[str]:
        # Examples:
        #   1. Only one arg on same line: "..."
        #   @custom:test:check:reverts ...
        #   2. No arg on same line, but multiple after: "- ... - ..."
        #   @custom:test:check:emits
        #   - ...
        #   - ...
        #   3. Arg on same line, and multiple after: "a,b,... - ... - ..."
        #   @custom:test:mark:parametrize a,b,...
        #   - ...
        #   - ...
        # TODO: Does `Solidity` parse them this same way? Should it be per compiler plugin?

        if raw_args := getattr(self, "_raw_args", None):

            # NOTE: Do `.lstrip("-")` on `raw_args` to remove first instance of `-`
            #       **in scenarios where we don't have an arg on the same line first**.
            return [ln.strip() for ln in raw_args.lstrip("-").split("-")]

        return []


class VyperTest(pytest.File, ManagerAccessMixin):
    @cached_property
    def compiler(self) -> "CompilerAPI":
        return self.compiler_manager.registered_compilers[".vy"]

    @cached_property
    def contract_type(self) -> "ContractType":
        # TODO: Use `settings=` for test-only settings?
        return self.compiler.compile_code(
            self.path.read_text(),
            # NOTE: Removes `test_`
            contractName=self.path.stem[5:],
        )

    @property
    def executor(self) -> "TestAccountAPI":
        return self.account_manager.test_accounts[-1]

    @cached_property
    def instance(self) -> "ContractInstance":
        if hasattr(instance := self.executor.deploy(self.contract_type), "setUp"):
            instance.setUp(sender=self.executor)

        self.snapshot = self.chain_manager.snapshot()
        return instance

    @cached_property
    def natspecs(self) -> dict[str, list[str]]:
        # TODO: Update `ethpm_types` to do this natively
        if natspecs := self.contract_type.natspecs:
            return {
                key: value.split("\n")
                for key, value in natspecs.items()
                if value
            }

        return {}

    def eval_arg(self, raw_arg: str) -> Any:
        # Just eval the whole string w/ global context from test
        # NOTE: This is potentially dangerous, but only run on your own tests!
        return eval(raw_arg, {}, {})

    def get_parametrized_args(self, spec: TestModifier) -> list[dict]:
        if spec is not TestModifier.MARK_PARAMETRIZE or len(spec.args) < 2:
            return []

        elif "," in (raw_arg_names := spec.args[0]):
            arg_names = [a.strip() for a in raw_arg_names.split(",")]
            return [
                dict(zip(arg_names, evaled_values, strict=True))
                for vals in spec.args[1:]
                if isinstance(evaled_values := self.eval_arg(vals), tuple)
                and len(evaled_values) == len(arg_names)
            ]

        else:
            return [{raw_arg_names: self.eval_arg(v)} for v in spec.args[1:]]

    def collect(self) -> Iterator["VyperTestcase"]:
        # NOTE: Only mutable calls that have names starting with `test_` will work
        for abi in self.contract_type.mutable_methods:
            if abi.name.startswith("test_"):
                # 1. First parse the natspec for that test to obtain any `@custom:test` modifiers
                modifiers: dict[str, dict[str, TestModifier]] = defaultdict(dict)
                for natspec in self.natspecs.get(abi.selector, []):
                    if modifier := TestModifier.parse_natspec(natspec):
                        # NOTE: Compilers do not allow the same @custom natspec twice
                        category, name = modifier.value.split(":")
                        modifiers[category][name] = modifier

                # 2. Yield test cases (multiple if `mark.parametrize` exists)
                if (parametrized := modifiers["mark"].pop("parametrize", None)):
                    # NOTE: If no cases collected, will not collect anything (fails silently)
                    for parametrized_args in self.get_parametrized_args(parametrized):
                        parametrized_str = "-".join(map(str, parametrized_args.values()))
                        yield VyperTestcase.from_parent(
                            self,
                            name=f"{abi.name}[{parametrized_str}]",
                            abi=abi,
                            checks=modifiers["check"],
                            marks=modifiers["mocks"],
                            parametrized_args=parametrized_args,
                        )

                else:
                    yield VyperTestcase.from_parent(
                        self,
                        name=abi.name,
                        abi=abi,
                        checks=modifiers["check"],
                        marks=modifiers["mocks"],
                        parametrized_args={},
                    )


class VyperTestcase(pytest.Item, ManagerAccessMixin):
    def __init__(
        self,
        *,
        abi: ABI,
        checks: dict[str, TestModifier],
        marks: dict[str, TestModifier],
        parametrized_args: dict,
        **kwargs,
    ):
        super().__init__(**kwargs)

        self.abi = abi
        self.checks = checks
        self.marks = marks
        self.parametrized_args = parametrized_args

        # TODO: Figure out a more "official" way to get fixtures by name
        # HACK: Otherwise `.get_fixture_value` doesn't work
        # NOTE: Copied this from pytest's own python test runner
        fm = self.session._fixturemanager
        fixtureinfo = fm.getfixtureinfo(node=self, func=None, cls=None)
        self._fixtureinfo = fixtureinfo
        self.fixturenames = fixtureinfo.names_closure
        # NOTE: Use `_ispytest=True` to avoid `PytestDeprecationWarning`
        self._request = TopRequest(self, _ispytest=True)

    @cached_property
    def method(self) -> "ContractMethodHandler":
        assert isinstance(self.parent, VyperTest)
        return getattr(self.parent.instance, self.name.split("[")[0])

    def get_fixture_value(self, fixture_name: str) -> Any | None:
        if fixture_defs := self.session._fixturemanager.getfixturedefs(fixture_name, self):
            return fixture_defs[0].execute(self._request)

        return None

    def get_value(self, abi_type: "ABIType") -> Any:
        if abi_type.name == "vm":
            # NOTE: Foundry stdlib's VM instance
            return "0x7109709ECfa91a80626fF3989D68f67F5b1DD12D"

        elif abi_type.name == "executor":
            assert isinstance(self.parent, VyperTest)  # mypy
            return self.parent.executor

        assert abi_type.name  # mypy happy (always true)
        if fixture_value := self.get_fixture_value(abi_type.name):
            return fixture_value

        if parameterized_value := self.parametrized_args.get(abi_type.name):
            return parameterized_value

        # TODO: Fuzzing strategy by `abi_type`, possible adapted w/ `@custom:test:strategy arg`?
        raise RuntimeError(
            f"Fuzzing '{abi_type.name}' by ABI strategy '{abi_type.canonical_type}' unsupported."
        )

    @cached_property
    def call_context(self) -> dict:
        assert isinstance(self.parent, VyperTest)  # mypy
        return {
            # 1. Contract instance (document not to use bare storage or internal calls)
            # Solidity instance (document not to use without `this.`)
            "this": self.parent.instance,
            # Vyper instance
            "self": self.parent.instance,
            # 2. Ape stuff
            "msg": type(
                "MsgContext",
                (object,),
                {"sender": self.parent.executor},
                # TODO: Other parts of `msg.` context?
            ),
            # TODO: Other evm stuff? e.g. `tx`, `block`, etc.
        }

    @cached_property
    def call_args(self) -> dict[str, Any]:
        return {ipt.name: self.get_value(ipt) for ipt in self.abi.inputs}

    def eval_arg(self, raw_arg: str) -> Any:
        assert isinstance(self.parent, VyperTest)  # mypy
        # Just eval the whole string w/ global/local context from case
        # NOTE: This is potentially dangerous, but only run on your own tests!
        return eval(raw_arg, self.call_context, self.call_args)

    def runtest(self):
        assert isinstance(self.parent, VyperTest)  # mypy
        if check_reverts := self.checks.get("reverts"):
            if len(args := check_reverts.args) < 1:
                raise AssertionError("Must provide an expected error message or type!")

            elif len(args := check_reverts.args) > 1:
                raise AssertionError("More than 1 expected error not supported!")

            # TODO: Use `RevertsManager`
            from ape import reverts

            with reverts(self.eval_arg(args[0])):
                self.method(
                    *self.call_args.values(),
                    sender=self.parent.executor,
                )

        else:
            # NOTE: Let revert bubble up naturally
            receipt = self.method(
                *self.call_args.values(),
                sender=self.parent.executor,
                raise_on_revert=(xfail := self.marks.get("xfail")) is not None,
            )

            if xfail and not receipt.error:
                raise AssertionError(
                    f"Expected '{self.name}' to fail for reason: '{xfail.args}'"
                )

            if check_emits := self.checks.get("emits"):
                expected_events = list(map(self.eval_arg, check_emits.args))
                assert receipt.events == expected_events

        # TODO: Test reporting functionality?


def pytest_collect_file(parent, file_path):
    if file_path.suffix == ".vy" and file_path.name.startswith("test_"):
        return VyperTest.from_parent(parent, path=file_path)
