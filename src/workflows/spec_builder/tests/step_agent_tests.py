"""Tests for the step-agent runtime."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from pydantic_ai.durable_exec.temporal import PydanticAIPlugin, PydanticAIWorkflow
from temporalio import workflow
from temporalio.api.enums.v1 import EventType
from temporalio.client import Client
from temporalio.worker import Worker

with workflow.unsafe.imports_passed_through():
    from src.workflows.spec_builder.step_agent import StepRequest, run_step, run_step_agent
    from src.workflows.spec_builder.tests.step_agent_fixtures.probe import PROBE


@workflow.defn
class ProbeWorkflow(PydanticAIWorkflow):
    """Runs the probe agent to its held call, approves it, and resumes."""

    __pydantic_ai_agents__ = [PROBE.agent]  # noqa: RUF012

    @workflow.run
    async def run(self, durable: bool) -> str:  # noqa: FBT001
        """Return the agent's final output."""
        held = await run_step(StepRequest(agent="probe", prompt="go"), durable=durable)
        assert held.held  # noqa: S101
        done = await run_step(
            StepRequest(
                agent="probe",
                message_history=held.message_history,
                approvals={call.tool_call_id: True for call in held.held},
            ),
            durable=durable,
        )
        assert done.output is not None  # noqa: S101
        return done.output


async def _activity_types(client: Client, *, durable: bool) -> tuple[str, set[str]]:
    async with Worker(
        client,
        task_queue=f"probe-{uuid.uuid4()}",
        workflows=[ProbeWorkflow],
        activities=[run_step_agent],
    ) as worker:
        handle = await client.start_workflow(
            ProbeWorkflow.run, durable, id=str(uuid.uuid4()), task_queue=worker.task_queue
        )
        output = await handle.result()
        scheduled = {
            event.activity_task_scheduled_event_attributes.activity_type.name
            async for event in handle.fetch_history_events()
            if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
        }
    return output, scheduled


@pytest.fixture
def plugin_client(client: Client) -> Client:
    """The shared test client; `conftest.py` installs the Pydantic AI plugin on it."""
    assert any(isinstance(p, PydanticAIPlugin) for p in client.config()["plugins"])  # noqa: S101
    return client


# The agent's MCP connection binds to the first event loop that uses it, so the tests share one.
@pytest.mark.asyncio(loop_scope="module")
class TestStepAgent:
    """Behavior tests for running a step agent plainly and durably."""

    async def test_run_should_hold_then_finish_when_run_without_a_temporal_server(self) -> None:
        """
        Scenario: Plain run
        Given an agent with a skill and an approval-held MCP tool
        When it runs outside any workflow
        Then it holds the tool call, and resumes to its output once the call is approved
        """
        first = await PROBE.run(StepRequest(agent="probe", prompt="go"))

        assert first.output is None
        assert [call.tool_name for call in first.held] == ["write_note"]

        second = await PROBE.run(
            StepRequest(
                agent="probe",
                message_history=first.message_history,
                approvals={call.tool_call_id: True for call in first.held},
            )
        )

        assert second.output == "done"
        assert second.held == []

    async def test_run_step_should_schedule_each_model_and_tool_call_as_an_activity_when_durable(
        self, plugin_client: Client
    ) -> None:
        """
        Scenario: Durable run
        Given the probe workflow runs with durable agents
        When it finishes
        Then the history schedules the agent's model and MCP tool calls as activities
        And it never schedules the whole-run activity
        """
        output, scheduled = await _activity_types(plugin_client, durable=True)

        assert output == "done"
        assert "agent__probe__model_request" in scheduled
        assert "agent__probe__mcp_server__notes__call_tool" in scheduled
        assert "run_step_agent" not in scheduled

    async def test_run_step_should_run_the_whole_agent_as_one_activity_when_not_durable(
        self, plugin_client: Client
    ) -> None:
        """
        Scenario: Plain run inside a workflow
        Given the probe workflow runs with durable agents off
        When it finishes
        Then the only activity scheduled is the whole-run activity
        """
        output, scheduled = await _activity_types(plugin_client, durable=False)

        assert output == "done"
        assert scheduled == {"run_step_agent"}


def test_readme_should_document_the_durability_setting_with_its_default() -> None:
    """
    Scenario: Documented setting
    Given the README's Agent Runtime section
    Then it names SPEC_BUILDER_DURABLE_AGENTS and its default of true
    """
    readme = (Path(__file__).parents[4] / "README.md").read_text()
    section = readme.split("### Agent Runtime", 1)[1].split("\n### ", 1)[0]

    assert "`SPEC_BUILDER_DURABLE_AGENTS` (default `true`)" in section
