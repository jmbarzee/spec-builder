"""Tests for holding an agent's marked tool calls for human approval."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from pydantic_ai.durable_exec.temporal import PydanticAIWorkflow
from temporalio import workflow
from temporalio.service import RPCError
from temporalio.worker import (
    Interceptor,
    Worker,
    WorkflowInboundInterceptor,
    WorkflowInterceptorClassInput,
    WorkflowOutboundInterceptor,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from temporalio.client import Client, WorkflowHandle
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import HandleSignalInput

with workflow.unsafe.imports_passed_through():
    from src.workflows.spec_builder.custom_types import (
        DecideToolCallsInput,
        SpecBuilderInput,
        SpecBuilderResult,
        SpecBuilderStatus,
    )
    from src.workflows.spec_builder.spec_builder_workflow import SpecBuilderWorkflow
    from src.workflows.spec_builder.tests.step_agent_fixtures.holder import HOLDER

CALLS = {"c1", "c2"}


@workflow.defn
class HeldStageWorkflow(SpecBuilderWorkflow, PydanticAIWorkflow):
    """The spec builder with a single agent stage, standing in for one a pipeline will have."""

    __pydantic_ai_agents__ = [HOLDER.agent]  # noqa: RUF012

    @workflow.run
    async def run(self, input: SpecBuilderInput) -> SpecBuilderResult:
        """Run the agent stage and report its output as a warning."""
        self._load_state(input)
        output = await self._run_agent_step(input, "holder", "Write the notes.")
        if output is not None:
            return self._result_from_state(status="completed", extra_warning=output)
        status = "cancelled" if self.state.canceled else "needs_clarification"
        return self._result_from_state(status=status)


class _RolloverSwitch:
    armed = False


class _RolloverInfo:
    def __init__(self, info: object, switch: _RolloverSwitch) -> None:
        self._info = info
        self._switch = switch

    def is_continue_as_new_suggested(self) -> bool:
        return self._switch.armed and self._info.continued_run_id is None

    def __getattr__(self, name: str) -> object:
        return getattr(self._info, name)


class _RolloverOutbound(WorkflowOutboundInterceptor):
    def __init__(self, next_: WorkflowOutboundInterceptor, switch: _RolloverSwitch) -> None:
        super().__init__(next_)
        self._switch = switch

    def info(self) -> _RolloverInfo:
        """Wrap the workflow info so it suggests continue-as-new when armed."""
        return _RolloverInfo(self.next.info(), self._switch)


class _RolloverInbound(WorkflowInboundInterceptor):
    """Suggests continue-as-new, in a first run only, once a tool decision has arrived."""

    def init(self, outbound: WorkflowOutboundInterceptor) -> None:
        self._switch = _RolloverSwitch()
        self.next.init(_RolloverOutbound(outbound, self._switch))

    async def handle_signal(self, input: HandleSignalInput) -> None:
        """Arm the switch on a tool decision, then handle the signal as usual."""
        if input.signal == "decide_tool_calls":
            self._switch.armed = True
        await self.next.handle_signal(input)


class RolloverOnDecisionInterceptor(Interceptor):
    """Worker interceptor that rolls a workflow over when it receives `decide_tool_calls`."""

    def workflow_interceptor_class(
        self, _input: WorkflowInterceptorClassInput
    ) -> type[WorkflowInboundInterceptor]:
        """Return the interceptor that arms on a tool decision."""
        return _RolloverInbound


def _input() -> SpecBuilderInput:
    return SpecBuilderInput(
        job_id=f"job-{uuid.uuid4()}", artifacts=[], output_targets=[], output_directory="."
    )


async def _status(
    handle: WorkflowHandle, *, until: Callable[[SpecBuilderStatus], bool] = lambda _status: True
) -> SpecBuilderStatus:
    """Poll the status query until `until` holds, riding out queries that land mid-rollover."""
    for _ in range(100):
        try:
            status = await handle.query(
                SpecBuilderWorkflow.get_status, rpc_timeout=timedelta(seconds=2)
            )
        except RPCError:
            status = None
        if status is not None and until(status):
            return status
        await asyncio.sleep(0.05)
    msg = f"status never matched: {status}"
    raise AssertionError(msg)


async def _held(handle: WorkflowHandle) -> SpecBuilderStatus:
    return await _status(handle, until=lambda status: bool(status.pending_tool_calls))


def _decide(decisions: dict[str, bool]) -> DecideToolCallsInput:
    return DecideToolCallsInput(request_id=str(uuid.uuid4()), decisions=decisions)


async def _start(client: Client, queue: str) -> WorkflowHandle:
    return await client.start_workflow(
        HeldStageWorkflow.run, _input(), id=str(uuid.uuid4()), task_queue=queue
    )


async def _rolled_over(client: Client, handle: WorkflowHandle) -> WorkflowHandle:
    """Wait for the workflow's first run to continue as new; return a handle on the latest run."""
    while True:
        run_id = (await client.get_workflow_handle(handle.id).describe()).run_id
        if run_id != handle.first_execution_run_id:
            break
        await asyncio.sleep(0.05)
    return client.get_workflow_handle(handle.id, run_id=run_id, result_type=SpecBuilderResult)


@pytest.fixture
def queue() -> str:
    """Return a task queue of its own."""
    return f"held-{uuid.uuid4()}"


# The agent's MCP connection binds to the first event loop that uses it, so the tests share one.
@pytest.mark.asyncio(loop_scope="module")
class TestHeldToolCalls:
    """Behavior tests for approval-held tool calls in the spec builder workflow."""

    @pytest.mark.timeout(30)
    async def test_decide_tool_calls_should_resume_the_stage_when_every_call_is_decided(
        self, client: Client, queue: str
    ) -> None:
        """
        Scenario: Hold and decide
        Given an agent stage holding two tool calls
        When one decision approves the first call and denies the second
        Then the live state lists both calls while held, and the stage resumes with the decisions
        """
        async with Worker(client, task_queue=queue, workflows=[HeldStageWorkflow]):
            handle = await _start(client, queue)
            status = await _held(handle)

            assert status.pending_human_action == "tool_approval"
            assert status.phase == "waiting_for_approval"
            assert {call.tool_call_id for call in status.pending_tool_calls} == CALLS
            assert {call.tool_name for call in status.pending_tool_calls} == {"write_note"}

            await handle.signal(
                HeldStageWorkflow.decide_tool_calls, _decide({"c1": True, "c2": False})
            )
            result = SpecBuilderResult.model_validate(await handle.result())

        assert result.status == "completed"
        assert "noted: alpha" in result.warnings[0]
        assert "noted: beta" not in result.warnings[0]

    @pytest.mark.timeout(30)
    async def test_decide_tool_calls_should_warn_and_keep_holding_when_calls_are_missing(
        self, client: Client, queue: str
    ) -> None:
        """
        Scenario: Partial decision
        Given an agent stage holding two tool calls
        When a decision names only one
        Then the workflow warns and keeps holding, and a complete decision then resumes it
        """
        async with Worker(client, task_queue=queue, workflows=[HeldStageWorkflow]):
            handle = await _start(client, queue)
            await _held(handle)

            await handle.signal(HeldStageWorkflow.decide_tool_calls, _decide({"c1": True}))
            status = await _status(handle, until=lambda current: bool(current.warnings))

            assert status.pending_human_action == "tool_approval"
            assert len(status.pending_tool_calls) == len(CALLS)

            await handle.signal(
                HeldStageWorkflow.decide_tool_calls, _decide({"c1": True, "c2": True})
            )
            result = SpecBuilderResult.model_validate(await handle.result())

        assert result.status == "completed"
        assert "noted: alpha | noted: beta" in result.warnings[-1]

    @pytest.mark.timeout(30)
    async def test_run_agent_step_should_resume_a_received_decision_when_it_continues_as_new(
        self, client: Client, queue: str
    ) -> None:
        """
        Scenario: Rollover with a decision received
        Given an agent stage holding two tool calls
        When the workflow continues as new as the decision arrives
        Then the next run resumes the stage with that decision
        """
        async with Worker(
            client,
            task_queue=queue,
            workflows=[HeldStageWorkflow],
            interceptors=[RolloverOnDecisionInterceptor()],
        ):
            handle = await _start(client, queue)
            await _held(handle)

            await handle.signal(
                HeldStageWorkflow.decide_tool_calls, _decide({"c1": False, "c2": True})
            )
            latest = await _rolled_over(client, handle)
            result = SpecBuilderResult.model_validate(await latest.result())

        assert result.status == "completed"
        assert "noted: beta" in result.warnings[0]
        assert "noted: alpha" not in result.warnings[0]

    @pytest.mark.timeout(30)
    async def test_run_agent_step_should_keep_the_hold_when_it_continues_as_new_undecided(
        self, client: Client, queue: str
    ) -> None:
        """
        Scenario: Rollover with calls pending
        Given an agent stage holding two tool calls
        When the workflow continues as new before any valid decision
        Then the next run lists the same calls, and a decision there resumes the stage
        """
        async with Worker(
            client,
            task_queue=queue,
            workflows=[HeldStageWorkflow],
            interceptors=[RolloverOnDecisionInterceptor()],
        ):
            handle = await _start(client, queue)
            await _held(handle)

            await handle.signal(HeldStageWorkflow.decide_tool_calls, _decide({"zz": True}))
            latest = await _rolled_over(client, handle)
            status = await _status(latest)

            assert status.continuation_count == 1
            assert {call.tool_call_id for call in status.pending_tool_calls} == CALLS
            assert status.warnings

            await latest.signal(
                HeldStageWorkflow.decide_tool_calls, _decide({"c1": True, "c2": True})
            )
            result = SpecBuilderResult.model_validate(await latest.result())

        assert result.status == "completed"

    @pytest.mark.timeout(30)
    async def test_run_agent_step_should_time_out_24_hours_after_the_hold_across_continue_as_new(
        self, client: Client, env: WorkflowEnvironment, queue: str
    ) -> None:
        """
        Scenario: Timeout across rollover
        Given an agent stage holding two tool calls that rolls over 10 hours into the hold
        When 23 hours 59 minutes have passed since the hold began
        Then the job still waits
        And two minutes later it has ended, needing clarification
        """
        async with Worker(
            client,
            task_queue=queue,
            workflows=[HeldStageWorkflow],
            interceptors=[RolloverOnDecisionInterceptor()],
        ):
            handle = await _start(client, queue)
            await _held(handle)
            await env.sleep(timedelta(hours=10))
            await handle.signal(HeldStageWorkflow.decide_tool_calls, _decide({"zz": True}))
            latest = await _rolled_over(client, handle)

            await env.sleep(timedelta(hours=13, minutes=59))
            status = await _status(latest)
            assert status.pending_human_action == "tool_approval"

            await env.sleep(timedelta(minutes=2))
            with env.auto_time_skipping_disabled():  # the result must not be reached by skipping
                result = SpecBuilderResult.model_validate(await latest.result())

        assert result.status == "needs_clarification"
