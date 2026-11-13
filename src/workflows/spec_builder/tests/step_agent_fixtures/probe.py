"""The agent the step-agent tests run: loads a skill, then calls an approval-held MCP tool."""

import sys
from pathlib import Path

from pydantic_ai.messages import (
    LoadCapabilityReturnPart,
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from src.workflows.spec_builder.step_agent import McpServer, StepAgent, StepAgentSpec

HERE = Path(__file__).resolve().parent


def _script(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
    returned = {
        part.tool_name
        for message in messages
        for part in getattr(message, "parts", [])
        if isinstance(part, ToolReturnPart | LoadCapabilityReturnPart)
    }
    if "load_capability" not in returned:
        return ModelResponse(parts=[ToolCallPart("load_capability", {"id": "note-taking"})])
    if "write_note" not in returned:
        return ModelResponse(parts=[ToolCallPart("write_note", {"text": "hi"})])
    return ModelResponse(parts=[TextPart("done")])


PROBE = StepAgent(
    StepAgentSpec(
        name="probe",
        model=FunctionModel(_script, model_name="stub"),
        instructions="Take the note.",
        skills_dir=HERE / "skills",
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
