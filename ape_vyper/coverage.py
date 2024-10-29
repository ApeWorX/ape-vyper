from fnmatch import fnmatch
from typing import TYPE_CHECKING, Optional

from ape.utils import ManagerAccessMixin
from ethpm_types.utils import SourceLocation

from ape_vyper.exceptions import RuntimeErrorType

if TYPE_CHECKING:
    from ape.types import ContractSourceCoverage
    from ethpm_types.source import ContractSource


class CoverageProfiler(ManagerAccessMixin):
    def __init__(self, source_coverage: "ContractSourceCoverage"):
        self._coverage = source_coverage

    def initialize(self, contract_source: "ContractSource"):
        exclusions = self.config_manager.get_config("test").coverage.exclude
        contract_name = contract_source.contract_type.name or "__UnknownContract__"

        # Check if excluding this contract.
        for exclusion in exclusions:
            if fnmatch(contract_name, exclusion.contract_name) and (
                not exclusion.method_name or exclusion.method_name == "*"
            ):
                # Skip this whole source.
                return

        contract_coverage = self._coverage.include(contract_name)

        def _exclude_fn(_name: str) -> bool:
            for _exclusion in exclusions:
                if fnmatch(contract_coverage.name, _exclusion.contract_name) and fnmatch(
                    _name, _exclusion.method_name
                ):
                    # This function should be skipped.
                    return True

            return False

        def _profile(_name: str, _full_name: str):
            # Ensure function isn't excluded.
            if _exclude_fn(_name):
                return

            _function_coverage = contract_coverage.include(_name, _full_name)

            # Only put the builtin-tags we expect users to be able to cover.
            tag = (
                str(item["dev"])
                if item.get("dev")
                and isinstance(item["dev"], str)
                and item["dev"].startswith("dev: ")
                and RuntimeErrorType.USER_ASSERT.value not in item["dev"]
                else None
            )
            _function_coverage.profile_statement(pc_int, location=location, tag=tag)

        # Some statements are too difficult to know right away where they belong,
        # such as statement related to kwarg-default auto-generated implicit lookups.
        # function_name -> (pc, location)
        pending_statements: dict[str, list[tuple[int, SourceLocation]]] = {}

        for pc, item in contract_source.pcmap.root.items():
            pc_int = int(pc)
            if pc_int < 0:
                continue

            location: Optional[SourceLocation]
            if item.get("location"):
                location_list = item["location"]
                if not isinstance(location_list, (list, tuple)):
                    raise TypeError(f"Unexpected location type '{type(location_list)}'.")

                # NOTE: Only doing 0 because mypy for some reason thinks it is optional.
                location = (
                    location_list[0] or 0,
                    location_list[1] or 0,
                    location_list[2] or 0,
                    location_list[3] or 0,
                )
            else:
                location = None

            if location is not None and not isinstance(location, tuple):
                # Only really for mypy.
                raise TypeError(f"Received unexpected type for location '{location}'.")

            if not location and not item.get("dev"):
                # Not a statement we can measure.
                continue

            if location:
                function = contract_source.lookup_function(location)
                if not function:
                    # Not sure if this happens.
                    continue

                matching_abis = [
                    a for a in contract_source.contract_type.methods if a.name == function.name
                ]
                if len(matching_abis) > 1:
                    # In Vyper, if there are multiple method ABIs with the same name,
                    # that is evidence of the default key-word argument generated methods.

                    is_part_of_signature = location[0] < function.offset
                    if is_part_of_signature and location[0] != location[2]:
                        # This likely is not a real statement, but not really sure what this is.
                        continue

                    # In Vyper, the ABI with the most inputs should be the one without extra steps.
                    longest_abi = max(matching_abis, key=lambda x: len(x.inputs))
                    if is_part_of_signature and longest_abi.name in pending_statements:
                        pending_statements[longest_abi.name].append((pc_int, location))
                    elif is_part_of_signature:
                        pending_statements[longest_abi.name] = [(pc_int, location)]
                    else:
                        # Put actual source statements under the ABI with all parameters as inputs.
                        _profile(longest_abi.name, longest_abi.selector)

                elif len(matching_abis) == 1:
                    _profile(function.name, matching_abis[0].selector)

                elif len(matching_abis) == 0:
                    # Is likely an internal method.
                    _profile(function.name, function.full_name or function.name)

            else:
                _profile("__builtin__", "__builtin__")

        if pending_statements:
            # Handle auto-generated kwarg-default statements here.
            # Sort each statement into buckets mapping to the method it belongs in.
            for fn_name, pending_ls in pending_statements.items():
                matching_abis = [
                    m for m in contract_source.contract_type.methods if m.name == fn_name
                ]
                longest_abi = max(matching_abis, key=lambda x: len(x.inputs))
                autogenerated_abis = [
                    abi for abi in matching_abis if abi.selector != longest_abi.selector
                ]
                # Sort the autogenerated ABIs so we can loop through them in the correct order.
                autogenerated_abis.sort(key=lambda a: len(a.inputs))
                buckets: dict[str, list[tuple[int, SourceLocation]]] = {
                    a.selector: [] for a in autogenerated_abis
                }
                selector_index = 0
                selector = autogenerated_abis[0].selector
                # Must loop through PCs from smallest to greatest for this to work.
                pending_ls.sort()
                jump_threshold = 10
                for _pc, loc in pending_ls:
                    if selector_index < len(autogenerated_abis):
                        selector = autogenerated_abis[selector_index].selector

                    if not buckets[selector]:
                        # No need for bounds checking when the bucket is empty.
                        buckets[selector].append((_pc, loc))
                        continue

                    last_pc = buckets[selector][-1][0]

                    # Check if jumped.
                    distance = _pc - last_pc
                    if distance > jump_threshold:
                        selector_index += 1
                        if selector_index >= len(autogenerated_abis):
                            break

                        selector = autogenerated_abis[selector_index].selector
                        buckets[selector].append((_pc, loc))
                    else:
                        buckets[selector].append((_pc, loc))

                for full_name, statements in buckets.items():
                    for _pc, location in statements:
                        if _exclude_fn(fn_name):
                            continue

                        function_coverage = contract_coverage.include(fn_name, full_name)
                        function_coverage.profile_statement(_pc, location=location)

        # After handling all methods with locations, let's also add the auto-getters,
        # which are not present in the source map.
        for method in contract_source.contract_type.view_methods:
            if method.selector not in [fn.full_name for fn in contract_coverage.functions]:
                if _exclude_fn(method.name):
                    return

                # Auto-getter found. Profile function without statements.
                contract_coverage.include(method.name, method.selector)

        return contract_coverage
