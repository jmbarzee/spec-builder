"""A stub-model agent that holds two approval-required MCP calls, then reports their returns."""

import sys

from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from src.workflows.spec_builder.step_agent import McpServer, StepAgent, StepAgentSpec
from src.workflows.spec_builder.tests.step_agent_fixtures.probe import HERE


def _script(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
    returns = [
        str(part.content)
        for message in messages
        for part in getattr(message, "parts", [])
        if isinstance(part, ToolReturnPart)
    ]
    if not returns:
        return ModelResponse(
            parts=[
                ToolCallPart("write_note", {"text": "alpha"}, tool_call_id="c1"),
                ToolCallPart("write_note", {"text": "beta"}, tool_call_id="c2"),
            ]
        )
    return ModelResponse(parts=[TextPart(" | ".join(returns))])


# Its own agent: an MCP connection binds to the loop that first runs it.
HOLDER = StepAgent(
    StepAgentSpec(
        name="holder",
        model=FunctionModel(_script, model_name="stub"),
        instructions="Write the notes.",
        mcp_servers=(
            McpServer(
                "notes",
                sys.executable,
                (str(HERE / "note_server.py"),),
                frozenset({"write_note"}),
            ),
        ),
    )
)
