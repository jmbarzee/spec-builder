"""Step-agent runtime: a Pydantic AI agent a stage runs durably or plainly.

Build each `StepAgent` at import time, in a module the workflow imports under
`workflow.unsafe.imports_passed_through()`, and list its `agent` in the workflow's
`__pydantic_ai_agents__`. Stage code calls `run_step`; `durable` picks whether each model and
tool call is its own activity or the whole run is one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from pydantic import BaseModel
from temporalio import activity, workflow
from temporalio.workflow import ActivityConfig

with workflow.unsafe.imports_passed_through():
    from fastmcp.client.transports import StdioTransport
    from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults
    from pydantic_ai.capabilities import LocalWorkspace
    from pydantic_ai.durable_exec.temporal import TemporalDurability
    from pydantic_ai.mcp import MCPToolset
    from pydantic_ai.messages import ModelMessage  # noqa: TC002 -- pydantic resolves it at runtime
    from pydantic_ai.models import Model  # noqa: TC002 -- dataclass field type
    from pydantic_ai_harness import Skills

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class McpServer:
    """A stdio MCP server the agent can call."""

    id: str
    command: str
    args: tuple[str, ...] = ()
    approval_required: frozenset[str] = frozenset()  # tool names held for approval


@dataclass(frozen=True)
class StepAgentSpec:
    """Everything that defines one agent."""

    name: str  # unique; prefixes the agent's activity names
    model: str | Model  # a `provider:model` string; a `Model` in tests
    instructions: str
    skills_dir: Path | None = None  # each subdirectory holds a SKILL.md
    mcp_servers: tuple[McpServer, ...] = ()
    timeout: timedelta = timedelta(minutes=5)


class HeldCall(BaseModel):
    """A tool call waiting for approval."""

    tool_call_id: str
    tool_name: str
    args: str  # JSON


class StepRequest(BaseModel):
    """One turn: a prompt, or a held history resumed with its approvals."""

    agent: str
    prompt: str | None = None
    message_history: list[ModelMessage] = []
    approvals: dict[str, bool] = {}  # tool_call_id -> approved


class StepResult(BaseModel):
    """The agent's output, or the calls held for approval; never both."""

    output: str | None
    held: list[HeldCall]
    message_history: list[ModelMessage]


_REGISTRY: dict[str, StepAgent] = {}


def _toolset(server: McpServer) -> MCPToolset:
    toolset = MCPToolset(
        StdioTransport(command=server.command, args=list(server.args)), id=server.id
    )
    if server.approval_required:
        return toolset.approval_required(
            lambda _ctx, tool, _args: tool.name in server.approval_required
        )
    return toolset


class StepAgent:
    """One Pydantic AI agent, registered by name."""

    def __init__(self, spec: StepAgentSpec) -> None:
        """Build the agent and register it under `spec.name`."""
        capabilities: list = []
        if spec.skills_dir is not None:
            capabilities += [LocalWorkspace(spec.skills_dir.parent), Skills(spec.skills_dir.name)]
        capabilities.append(
            TemporalDurability(activity_config=ActivityConfig(start_to_close_timeout=spec.timeout))
        )
        self.spec = spec
        self.agent = Agent(
            spec.model,
            name=spec.name,
            instructions=spec.instructions,
            output_type=[str, DeferredToolRequests],
            toolsets=[_toolset(server) for server in spec.mcp_servers],
            capabilities=capabilities,
        )
        _REGISTRY[spec.name] = self

    async def run(self, request: StepRequest) -> StepResult:
        """Run in the caller's context: plainly outside a workflow, as activities inside one."""
        result = await self.agent.run(
            request.prompt,
            message_history=request.message_history or None,
            deferred_tool_results=(
                DeferredToolResults(approvals=request.approvals) if request.approvals else None
            ),
        )
        output = result.output
        held = (
            [
                HeldCall(
                    tool_call_id=call.tool_call_id,
                    tool_name=call.tool_name,
                    args=call.args_as_json_str(),
                )
                for call in output.approvals
            ]
            if isinstance(output, DeferredToolRequests)
            else []
        )
        return StepResult(
            output=None if held else output,
            held=held,
            message_history=result.all_messages(),
        )


@activity.defn
async def run_step_agent(request: StepRequest) -> StepResult:
    """Run a whole agent as one activity; a retry repeats the whole run."""
    return await _REGISTRY[request.agent].run(request)


async def run_step(request: StepRequest, *, durable: bool) -> StepResult:
    """Run a step from workflow code; `durable=False` makes the whole run one activity."""
    agent = _REGISTRY[request.agent]
    if durable or not workflow.in_workflow():
        return await agent.run(request)
    return await workflow.execute_activity(
        run_step_agent, request, start_to_close_timeout=agent.spec.timeout
    )
