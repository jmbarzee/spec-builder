"""The design agent: writes `.twf` from a markdown spec, following temporal-architect's skill."""

from __future__ import annotations

from datetime import timedelta

from src.workflows.spec_builder.architecture import SKILLS_DIR
from src.workflows.spec_builder.model_executor import load_project_dotenv, resolve_model
from src.workflows.spec_builder.step_agent import McpServer, StepAgent, StepAgentSpec

DESIGN_AGENT_NAME = "design"
DESIGN_SKILL = "temporal-architect-design"
INSTRUCTIONS = (
    f"You design Temporal systems in `.twf`. Load the `{DESIGN_SKILL}` skill and follow it. "
    "Check your design with the `twf_*` tools before you answer. "
    "Reply with the complete `.twf` design and nothing else."
)

load_project_dotenv()  # `SPEC_BUILDER_DESIGN_MODEL` may live in `.env`, and the agent reads it now
DESIGN_AGENT = StepAgent(
    StepAgentSpec(
        name=DESIGN_AGENT_NAME,
        model=resolve_model("design"),
        instructions=INSTRUCTIONS,
        skills_dir=SKILLS_DIR,
        skills=frozenset({DESIGN_SKILL}),
        mcp_servers=(McpServer("twf", "twf", ("mcp",)),),
        timeout=timedelta(minutes=10),
    )
)
