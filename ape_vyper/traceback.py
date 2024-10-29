from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Optional, cast

from ape.types import SourceTraceback
from ape.utils import ManagerAccessMixin, get_full_extension
from eth_pydantic_types import HexBytes
from ethpm_types import PCMap
from ethpm_types.source import ContractSource, Function
from evm_trace import TraceFrame
from evm_trace.enums import CALL_OPCODES
from evm_trace.geth import create_call_node_data

from ape_vyper._utils import DEV_MSG_PATTERN, RETURN_OPCODES, FileType
from ape_vyper.exceptions import RuntimeErrorType

if TYPE_CHECKING:
    from ape.managers.project import ProjectManager


class SourceTracer(ManagerAccessMixin):
    """
    Use EVM data to create a trace of Vyper source lines.
    """

    @classmethod
    def trace(
        cls,
        frames: Iterator[dict],
        contract: ContractSource,
        calldata: HexBytes,
        previous_depth: Optional[int] = None,
        project: Optional["ProjectManager"] = None,
    ) -> SourceTraceback:
        pm = project or cls.local_project
        method_id = HexBytes(calldata[:4])
        traceback = SourceTraceback.model_validate([])
        completed = False
        pcmap = PCMap.model_validate({})

        for frame in frames:
            if frame["op"] in [c.value for c in CALL_OPCODES]:
                start_depth = frame["depth"]
                called_contract, sub_calldata = cls._create_contract_from_call(frame, project=pm)
                if called_contract:
                    ext = get_full_extension(Path(called_contract.source_id))
                    if ext in [x for x in FileType]:
                        # Called another Vyper contract.
                        sub_trace = cls.trace(
                            frames,
                            called_contract,
                            sub_calldata,
                            previous_depth=frame["depth"],
                            project=pm,
                        )
                        traceback.extend(sub_trace)

                    else:
                        # Not a Vyper contract!
                        compiler = cls.compiler_manager.registered_compilers[ext]
                        try:
                            sub_trace = compiler.trace_source(
                                called_contract.contract_type, frames, sub_calldata
                            )
                            traceback.extend(sub_trace)
                        except NotImplementedError:
                            # Compiler not supported. Fast forward out of this call.
                            for fr in frames:
                                if fr["depth"] <= start_depth:
                                    break

                            continue

                else:
                    # Contract not found. Fast forward out of this call.
                    for fr in frames:
                        if fr["depth"] <= start_depth:
                            break

                    continue

            elif frame["op"] in RETURN_OPCODES:
                # For the base CALL, don't mark as completed until trace is gone.
                # This helps in cases where we failed to detect a subcall properly.
                completed = previous_depth is not None

            pcs_to_try_adding = set()
            if "PUSH" in frame["op"] and frame["pc"] in contract.pcmap:
                # Check if next op is SSTORE to properly use AST from push op.
                next_frame: Optional[dict] = frame
                loc = contract.pcmap[frame["pc"]]
                pcs_to_try_adding.add(frame["pc"])

                while next_frame and "PUSH" in next_frame["op"]:
                    next_frame = next(frames, None)
                    if next_frame and "PUSH" in next_frame["op"]:
                        pcs_to_try_adding.add(next_frame["pc"])

                is_non_payable_hit = False
                if next_frame and next_frame["op"] == "SSTORE":
                    push_location = tuple(loc["location"])  # type: ignore
                    pcmap = PCMap.model_validate({next_frame["pc"]: {"location": push_location}})

                elif next_frame and next_frame["op"] in RETURN_OPCODES:
                    completed = True

                else:
                    pcmap = contract.pcmap
                    dev_val = str((loc.get("dev") or "")).replace("dev: ", "")
                    is_non_payable_hit = dev_val == RuntimeErrorType.NONPAYABLE_CHECK.value

                if not is_non_payable_hit and next_frame:
                    frame = next_frame

            else:
                pcmap = contract.pcmap

            pcs_to_try_adding.add(frame["pc"])
            pcs_to_try_adding = {pc for pc in pcs_to_try_adding if pc in pcmap}
            if not pcs_to_try_adding:
                if (
                    frame["op"] == "REVERT"
                    and frame["pc"] + 1 in pcmap
                    and RuntimeErrorType.USER_ASSERT.value
                    in str(pcmap[frame["pc"] + 1].get("dev", ""))
                ):
                    # Not sure why this happens. Maybe an off-by-1 bug in Vyper.
                    pcs_to_try_adding.add(frame["pc"] + 1)

            pc_groups: list[list] = []
            for pc in pcs_to_try_adding:
                location = (
                    cast(tuple[int, int, int, int], tuple(pcmap[pc].get("location") or [])) or None
                )
                dev_item = pcmap[pc].get("dev", "")
                dev = str(dev_item).replace("dev: ", "")

                done = False
                for group in pc_groups:
                    if group[0] != location:
                        continue

                    group[1].add(pc)
                    dev = group[2] = dev or group[2]
                    done = True
                    break

                if not done:
                    # New group.
                    pc_groups.append([location, {pc}, dev])

            dev_messages = contract.contract_type.dev_messages or {}
            for location, pcs, dev in pc_groups:
                if dev in [m.value for m in RuntimeErrorType if m != RuntimeErrorType.USER_ASSERT]:
                    error_type = RuntimeErrorType(dev)
                    if (
                        error_type != RuntimeErrorType.NONPAYABLE_CHECK
                        and traceback.last is not None
                    ):
                        # If the error type is not the non-payable check,
                        # it happened in the last method.
                        name = traceback.last.closure.name
                        full_name = traceback.last.closure.full_name

                    elif method_id in contract.contract_type.methods:
                        # For non-payable checks, they should hit here.
                        method_checked = contract.contract_type.methods[method_id]
                        name = method_checked.name
                        full_name = method_checked.selector

                    else:
                        # Not sure if possible to get here.
                        name = error_type.name.lower()
                        full_name = name

                    if (
                        dev == RuntimeErrorType.INVALID_CALLDATA_OR_VALUE.value
                        and len(traceback.source_statements) > 0
                    ):
                        # NOTE: Skip adding invalid calldata / value checks when
                        # we have already hit source statements. The reason for this
                        # is because of misleading Vyper optimizations sharing revert PCs.
                        continue

                    # Empty source (is builtin)
                    traceback.add_builtin_jump(
                        name,
                        f"dev: {dev}",
                        full_name=full_name,
                        pcs=pcs,
                        source_path=contract.source_path,
                    )
                    continue

                elif not location:
                    # Unknown.
                    continue

                if not (function := contract.lookup_function(location, method_id=method_id)):
                    continue

                if (
                    not traceback.last
                    or traceback.last.closure.full_name != function.full_name
                    or not isinstance(traceback.last.closure, Function)
                ):
                    depth = (
                        frame["depth"] + 1
                        if traceback.last and traceback.last.depth == frame["depth"]
                        else frame["depth"]
                    )

                    traceback.add_jump(
                        location,
                        function,
                        depth,
                        pcs=pcs,
                        source_path=contract.source_path,
                    )
                else:
                    traceback.extend_last(location, pcs=pcs)

                if len(traceback.source_statements) > 0:
                    last_statement = traceback.source_statements[-1]
                    if dev.endswith(RuntimeErrorType.USER_ASSERT.value) or any(
                        DEV_MSG_PATTERN.match(str(s)) for s in str(last_statement).splitlines()
                    ):
                        # Add dev message to user assert
                        for lineno in range(
                            last_statement.end_lineno, last_statement.begin_lineno - 1, -1
                        ):
                            if lineno in dev_messages:
                                last_statement.type = dev_messages[lineno]

            if completed:
                break

        return traceback

    @classmethod
    def _create_contract_from_call(
        cls, frame: dict, project: Optional["ProjectManager"] = None
    ) -> tuple[Optional[ContractSource], HexBytes]:
        pm = project or cls.local_project
        evm_frame = TraceFrame(**frame)
        data = create_call_node_data(evm_frame)
        calldata = data.get("calldata", HexBytes(""))
        if not (address := (data.get("address", evm_frame.contract_address) or None)):
            return None, calldata

        try:
            address = cls.provider.network.ecosystem.decode_address(address)
        except Exception:
            return None, calldata

        if address not in cls.chain_manager.contracts:
            return None, calldata

        called_contract = cls.chain_manager.contracts[address]
        return pm._create_contract_source(called_contract), calldata
