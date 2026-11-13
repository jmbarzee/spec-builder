"""Tests for the design stage. These run the real `twf` gate and `twf mcp`, with a stub model."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from pydantic_ai.exceptions import UserError
from pydantic_ai.messages import (
    LoadCapabilityReturnPart,
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.google import GoogleModel
from temporalio import activity
from temporalio.client import Client
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

from src.workflows.spec_builder import spec_builder_activities, step_agent
from src.workflows.spec_builder.architecture import SKILLS_DIR
from src.workflows.spec_builder.custom_types import (
    SPEC_BUILDER_TASK_QUEUE,
    SpecBuilderInput,
    SpecBuilderOptions,
    SpecBuilderResult,
    ValidateTwfInput,
    ValidateTwfOutput,
)
from src.workflows.spec_builder.design_agent import DESIGN_SKILL
from src.workflows.spec_builder.design_stage_workflow import DesignStageWorkflow
from src.workflows.spec_builder.model_executor import resolve_model
from src.workflows.spec_builder.spec_builder_activities import validate_twf_design
from src.workflows.spec_builder.spec_builder_workflow import SpecBuilderWorkflow
from src.workflows.spec_builder.step_agent import run_step_agent
from src.workflows.spec_builder.temporal_best_practices_review_workflow import (
    TemporalBestPracticesReviewWorkflow,
)
from src.workflows.spec_builder.tests.spec_builder_workflow_tests import build_mock_activities

VALID_DESIGN = 'workflow Foo():\n\tactivity Bar()\n\nactivity Bar() -> (string):\n\treturn "x"\n'
INVALID_DESIGN = "workflow Foo():\n\tactivity Bar()\n"
REFERENCE = "temporal-architect-design/reference/common-errors.md"
WORKFLOWS = [SpecBuilderWorkflow, TemporalBestPracticesReviewWorkflow, DesignStageWorkflow]


class StubDesigner:
    """A stand-in model: it uses the skill, a reference and `twf_check`, then answers by attempt."""

    def __init__(self, *designs: str) -> None:
        self.designs = designs
        self.tools: set[str] = set()
        self.prompts: list[str] = []
        self.returned = ""

    def __call__(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.tools |= {tool.name for tool in info.function_tools}
        parts = [part for message in messages for part in message.parts]
        self.prompts = [part.content for part in parts if isinstance(part, UserPromptPart)]
        answers = sum(isinstance(part, TextPart) for part in parts)
        returned = [
            part for part in parts if isinstance(part, ToolReturnPart | LoadCapabilityReturnPart)
        ]
        self.returned = "\n".join(str(part.content) for part in returned)
        names = {part.tool_name for part in returned}
        if answers == 0:
            if "load_capability" not in names:
                return _call("load_capability", {"id": DESIGN_SKILL})
            if "read_file" not in names:
                return _call("read_file", {"path": REFERENCE})
            if "twf_check" not in names:
                return _call("twf_check", {"source": self.designs[0]})
        return ModelResponse(parts=[TextPart(self.designs[min(answers, len(self.designs) - 1)])])


def _call(tool: str, args: dict[str, str]) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool, args)])


@pytest.fixture
def designer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[StubDesigner]:
    """Return a list the test fills with one stub; the real model string resolves to it."""
    stubs: list[StubDesigner] = []
    monkeypatch.setattr(
        step_agent, "_build_model", lambda _id: FunctionModel(stubs[0], model_name="stub")
    )
    monkeypatch.setattr(spec_builder_activities, "RUNTIME_ROOT", tmp_path / "runtime")
    return stubs


def _input(tmp_path: Path, *, durable: bool = True, revisions: int = 2) -> SpecBuilderInput:
    source = tmp_path / "input.md"
    source.write_text("# Input\n\nTemporal workflow notes.\n", encoding="utf-8")
    return SpecBuilderInput(
        job_id=f"job-{uuid.uuid4()}",
        artifacts=[
            {"artifact_id": "doc-001", "uri": str(source), "type": "markdown", "title": "Notes"}
        ],
        output_targets=["markdown_spec"],
        output_directory=str(tmp_path / "output"),
        options=SpecBuilderOptions(durable_agents=durable, max_twf_revisions=revisions),
        architecture_mode=True,
    )


async def _run(
    client: Client,
    input_data: SpecBuilderInput,
    *,
    scenario: str = "happy_path",
    gate: object = validate_twf_design,
) -> tuple[SpecBuilderResult, list[str], list[str]]:
    """Run the parent workflow; return its result, activity names and child workflow types."""
    activities, _counters = build_mock_activities(scenario)
    async with Worker(
        client,
        task_queue=SPEC_BUILDER_TASK_QUEUE,
        workflows=WORKFLOWS,
        activities=[*activities, gate, run_step_agent],
    ):
        handle = await client.start_workflow(
            SpecBuilderWorkflow.run,
            input_data,
            id=f"spec-builder-{uuid.uuid4()}",
            task_queue=SPEC_BUILDER_TASK_QUEUE,
        )
        result = SpecBuilderResult.model_validate(await handle.result())
        parent = (await handle.fetch_history()).events
        children = [
            event.start_child_workflow_execution_initiated_event_attributes
            for event in parent
            if event.HasField("start_child_workflow_execution_initiated_event_attributes")
        ]
        design = [child for child in children if child.workflow_type.name == "DesignStageWorkflow"]
        scheduled: list[str] = []
        for child in design:
            events = (await client.get_workflow_handle(child.workflow_id).fetch_history()).events
            scheduled += [
                event.activity_task_scheduled_event_attributes.activity_type.name
                for event in events
                if event.HasField("activity_task_scheduled_event_attributes")
            ]
    return result, scheduled, [child.workflow_type.name for child in children]


# The agent's MCP connection binds to the first event loop that uses it, so the tests share one.
@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.timeout(120)
class TestDesignStage:
    """Behavior tests for the design stage child workflow and its parent wiring."""

    @pytest.mark.parametrize("durable", [True, False])
    async def test_should_design_the_accepted_spec_with_the_skill_and_twf_tools(
        self, client: Client, designer: list[StubDesigner], tmp_path: Path, *, durable: bool
    ) -> None:
        """
        Scenario: A valid design
        Given an architecture-mode job whose design agent reads its skill, a reference and checks
        When the spec completes
        Then a design child runs after the draft with the skill, read-only file tools and twf tools
        And the gate's verdict is valid, its artifacts join the job's, and nothing warns
        """
        stub = StubDesigner(VALID_DESIGN)
        designer.append(stub)

        result, scheduled, children = await _run(client, _input(tmp_path, durable=durable))

        assert children == ["TemporalBestPracticesReviewWorkflow", "DesignStageWorkflow"]
        assert {"twf_check", "twf_spec_get", "read_file", "load_capability"} <= stub.tools
        assert not {"write_file", "edit_file", "delete_file"} & stub.tools
        reference_line = (SKILLS_DIR / REFERENCE).read_text().splitlines()[2][:40]
        assert "Temporal Architect: System Design" in "\n".join(stub.prompts)  # the skill body
        assert reference_line in stub.returned  # a file the skill links to
        assert "'diagnostics': []" in stub.returned  # `twf_check` ran and found nothing
        assert result.status == "completed"
        assert result.warnings == []
        assert {"twf_source", "twf_parse_json", "twf_graph_json"} <= set(result.artifact_uris)
        written = Path(result.artifact_uris["twf_source"].removeprefix("file://")).read_text()
        assert written.strip() == VALID_DESIGN.strip()
        assert scheduled.count("validate_twf_design") == 1
        assert ("run_step_agent" in scheduled) is (not durable)
        assert ("agent__design__model_request" in scheduled) is durable

    async def test_should_design_only_the_spec_the_supervisor_accepts(
        self, client: Client, designer: list[StubDesigner], tmp_path: Path
    ) -> None:
        """
        Scenario: A draft sent back for revision
        Given the supervisor rejects the first draft and accepts the second
        When the job completes
        Then exactly one design runs, and it prompts with the second draft
        """
        stub = StubDesigner(VALID_DESIGN)
        designer.append(stub)

        _result, _scheduled, children = await _run(
            client, _input(tmp_path), scenario="revise_from_draft"
        )

        assert children.count("DesignStageWorkflow") == 1
        assert "# Draft 2" in stub.prompts[0]

    async def test_should_repair_the_design_with_the_gates_diagnostics(
        self, client: Client, designer: list[StubDesigner], tmp_path: Path
    ) -> None:
        """
        Scenario: A design that needs one repair
        Given the agent first answers with an undefined activity, then a valid design
        When the stage runs
        Then the second prompt lists the diagnostic as line:column CODE message
        And the job completes with the valid design and no warning
        """
        stub = StubDesigner(INVALID_DESIGN, VALID_DESIGN)
        designer.append(stub)

        result, scheduled, _children = await _run(client, _input(tmp_path))

        assert scheduled.count("validate_twf_design") == 2
        assert "2:11 UNDEFINED_ACTIVITY" in stub.prompts[-1]
        assert result.warnings == []

    @pytest.mark.parametrize("revisions", [0, 1])
    async def test_should_stop_at_the_revision_budget_and_warn_when_the_design_stays_invalid(
        self, client: Client, designer: list[StubDesigner], tmp_path: Path, revisions: int
    ) -> None:
        """
        Scenario: A design that never becomes valid
        Given an agent that always answers with an undefined activity
        When the stage runs with a revision budget
        Then it makes one attempt plus the budget, and the job warns but still completes
        """
        designer.append(StubDesigner(INVALID_DESIGN))

        result, scheduled, _children = await _run(client, _input(tmp_path, revisions=revisions))

        attempts = 1 + revisions
        assert scheduled.count("validate_twf_design") == attempts
        assert result.status == "completed"
        assert result.markdown_spec is not None
        assert result.warnings == [f"Design still has 1 parser errors after {attempts} attempts."]
        assert "twf_source" in result.artifact_uris  # the invalid design is still inspectable

    async def test_should_warn_and_complete_when_the_design_stage_fails(
        self, client: Client, designer: list[StubDesigner], tmp_path: Path
    ) -> None:
        """
        Scenario: A design outage
        Given the validation gate fails for good
        When the stage runs
        Then the job warns with the cause and still completes with its spec
        """
        designer.append(StubDesigner(VALID_DESIGN))

        @activity.defn(name="validate_twf_design")
        async def broken_gate(_input: ValidateTwfInput) -> ValidateTwfOutput:
            raise ApplicationError("twf exploded", non_retryable=True)

        result, _scheduled, _children = await _run(client, _input(tmp_path), gate=broken_gate)

        assert result.status == "completed"
        assert result.warnings == ["Design stage failed: twf exploded"]


def test_design_model_should_default_to_the_google_gemini_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Scenario: Default design model
    Given no design model setting and a Gemini key
    When the design model string resolves
    Then it builds a Google model, from the `google:` prefix
    """
    monkeypatch.delenv("SPEC_BUILDER_DESIGN_MODEL", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    model = step_agent._build_model(resolve_model("design"))

    assert resolve_model("design") == "google:gemini-3.1-pro-preview"
    assert isinstance(model, GoogleModel)


@pytest.mark.parametrize("durable", [True, False])
@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.timeout(120)
async def test_should_warn_and_complete_when_the_design_model_cannot_be_built(
    client: Client,
    designer: list[StubDesigner],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    durable: bool,
) -> None:
    """
    Scenario: A model with no key
    Given the design model's provider has no key
    When the stage runs, durably or as one activity
    Then the stage fails without retrying forever, and the job warns and completes
    """

    def no_key(_model_id: str) -> None:
        msg = "no provider key"
        raise UserError(msg)

    monkeypatch.setattr(step_agent, "_build_model", no_key)

    result, _scheduled, _children = await _run(client, _input(tmp_path, durable=durable))

    assert result.status == "completed"
    assert "Design stage failed" in result.warnings[0]
    assert "no provider key" in result.warnings[0]
