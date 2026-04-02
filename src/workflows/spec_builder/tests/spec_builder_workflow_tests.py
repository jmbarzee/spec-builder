"""Workflow tests for the spec builder."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager

import pytest
from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker

from src.workflows.spec_builder.acquire_content_workflow import AcquireContentWorkflow
from src.workflows.spec_builder.custom_types import (
    AcquireContentInput,
    AcquisitionOutput,
    AcquiredArtifact,
    ApproveDraftInput,
    ArtifactInput,
    BestPracticesPassage,
    BestPracticesReviewOutput,
    CancelJobInput,
    CriticOutput,
    DraftSpecInput,
    DraftSpecOutput,
    IntentClassificationOutput,
    PersistFinalOutputsInput,
    PersistFinalOutputsOutput,
    RejectDraftInput,
    RunBestPracticesReviewActivityInput,
    SPEC_BUILDER_TASK_QUEUE,
    SpecBuilderInput,
    SpecBuilderOptions,
    SpecBuilderResult,
    SpecBuilderStatus,
    StructuredExtractionInput,
    StructuredExtractionOutput,
    StructuredEntity,
    StructuredFact,
    StructuredRelationship,
    StructuredSpec,
    StructuredSpecComponent,
    SubmitClarificationInput,
    SupervisorDecision,
    SupervisorDecisionInput,
    TemporalBestPracticesReviewInput,
    TraceabilityEntry,
    ContinueAsNewThreshold,
)
from src.workflows.spec_builder.draft_spec_workflow import DraftSpecWorkflow
from src.workflows.spec_builder.extraction_critic_workflow import ExtractionCriticWorkflow
from src.workflows.spec_builder.intent_classification_workflow import (
    IntentClassificationWorkflow,
)
from src.workflows.spec_builder.spec_builder_workflow import SpecBuilderWorkflow
from src.workflows.spec_builder.structured_extraction_workflow import (
    StructuredExtractionWorkflow,
)
from src.workflows.spec_builder.supervisor_decision_workflow import (
    SupervisorDecisionWorkflow,
)
from src.workflows.spec_builder.temporal_best_practices_review_workflow import (
    TemporalBestPracticesReviewWorkflow,
)


def build_structured_extraction() -> StructuredExtractionOutput:
    """Return a compact extraction fixture."""
    return StructuredExtractionOutput(
        entities=[
            StructuredEntity(
                id="workflow-parent",
                type="workflow",
                name="SpecBuilderWorkflow",
                source_artifact_ids=["doc-001"],
            )
        ],
        relationships=[
            StructuredRelationship(
                type="contains",
                **{"from": "workflow-parent", "to": "activity-draft"},
                source_artifact_ids=["doc-001"],
            )
        ],
        facts=[
            StructuredFact(
                statement="The workflow must remain deterministic.",
                classification="explicit",
                confidence=0.99,
                source_artifact_ids=["doc-001"],
            )
        ],
        ambiguities=[],
        artifact_uri="file:///tmp/extraction.json",
    )


def build_draft(version: int = 1) -> DraftSpecOutput:
    """Return a compact draft fixture."""
    return DraftSpecOutput(
        markdown_spec=f"# Draft {version}\n\nThe workflow must remain deterministic.\n",
        structured_spec=StructuredSpec(
            title=f"Draft {version}",
            objective="Build an implementation-ready Temporal spec.",
            scope=["Generate markdown and structured JSON output."],
            components=[
                StructuredSpecComponent(
                    name="SpecBuilderWorkflow",
                    responsibilities=["Coordinate fixed child-workflow stages."],
                )
            ],
            execution_flow=["Acquire", "Extract", "Classify", "Draft", "Review"],
            data_contracts=[{"name": "SpecBuilderInput"}],
            error_handling=["Retry transient activity failures."],
            observability=["Emit stage start and end logs."],
            assumptions=["Artifacts are local files."],
            open_questions=[],
            traceability=[
                TraceabilityEntry(
                    claim="The workflow must remain deterministic.",
                    classification="explicit",
                    source_artifact_ids=["doc-001"],
                )
            ],
        ),
        assumptions=["Artifacts are local files."],
        open_questions=[],
        summary=f"Draft summary {version}",
        draft_version=version,
        markdown_spec_uri=f"file:///tmp/draft-{version}.md",
        structured_spec_uri=f"file:///tmp/draft-{version}.json",
    )


def build_mock_activities(
    scenario: str,
) -> tuple[list[Callable[..., object]], dict[str, int]]:
    """Build mocked activities for a workflow scenario."""
    counters: dict[str, int] = {
        "acquire": 0,
        "extract": 0,
        "classify": 0,
        "critic": 0,
        "draft": 0,
        "review": 0,
        "supervisor": 0,
        "persist": 0,
    }

    if scenario == "happy_path":
        critic_actions = ["proceed"]
        supervisor_actions = ["accept"]
    elif scenario == "revise_from_draft":
        critic_actions = ["proceed", "proceed"]
        supervisor_actions = ["revise_from_draft", "accept"]
    elif scenario == "revise_from_extraction":
        critic_actions = ["proceed", "proceed"]
        supervisor_actions = ["revise_from_extraction", "accept"]
    elif scenario == "ask_human":
        critic_actions = ["proceed", "proceed"]
        supervisor_actions = ["ask_human", "accept"]
    elif scenario == "approval":
        critic_actions = ["proceed"]
        supervisor_actions = ["accept"]
    elif scenario == "reject_draft":
        critic_actions = ["proceed", "proceed"]
        supervisor_actions = ["accept", "accept"]
    elif scenario == "reject_acquire":
        critic_actions = ["proceed", "proceed"]
        supervisor_actions = ["accept", "accept"]
    elif scenario == "continue_as_new":
        critic_actions = ["proceed", "proceed"]
        supervisor_actions = ["revise_from_draft", "accept"]
    elif scenario == "cancel":
        critic_actions = ["proceed"]
        supervisor_actions = ["ask_human"]
    else:
        raise AssertionError(f"Unknown scenario: {scenario}")

    @activity.defn(name="acquire_content")
    async def acquire_content(input: AcquireContentInput) -> AcquisitionOutput:
        counters["acquire"] += 1
        return AcquisitionOutput(
            source_bundle_id=f"bundle-{counters['acquire']}",
            artifacts=[
                AcquiredArtifact(
                    artifact_id=input.artifacts[0].artifact_id,
                    type=input.artifacts[0].type,
                    extracted_text="normalized text",
                    confidence=1.0,
                    source_uri="file:///tmp/source.md",
                    normalized_text_uri="file:///tmp/source-normalized.md",
                )
            ],
            warnings=[],
            source_bundle_uri=f"file:///tmp/bundle-{counters['acquire']}.json",
        )

    @activity.defn(name="run_structured_extraction")
    async def run_structured_extraction(input: StructuredExtractionInput) -> StructuredExtractionOutput:
        counters["extract"] += 1
        return build_structured_extraction()

    @activity.defn(name="run_intent_classification")
    async def run_intent_classification(input) -> IntentClassificationOutput:
        counters["classify"] += 1
        return IntentClassificationOutput(
            primary_intent="implementation_spec",
            target_audience="codex",
            expected_output_formats=["markdown", "json"],
            best_practices_profile="temporal_workflow",
            confidence=0.95,
            artifact_uri="file:///tmp/intent.json",
        )

    @activity.defn(name="run_extraction_critic")
    async def run_extraction_critic(input) -> CriticOutput:
        counters["critic"] += 1
        action = critic_actions[min(counters["critic"] - 1, len(critic_actions) - 1)]
        return CriticOutput(
            score=0.95,
            blocking_issues=[],
            non_blocking_issues=[],
            missing_information=["Clarify GUI approval requirements."] if action == "ask_human" else [],
            recommended_action=action,
            artifact_uri=f"file:///tmp/critic-{counters['critic']}.json",
        )

    @activity.defn(name="run_draft_spec")
    async def run_draft_spec(input: DraftSpecInput) -> DraftSpecOutput:
        counters["draft"] += 1
        return build_draft(counters["draft"])

    @activity.defn(name="retrieve_best_practices_passages")
    async def retrieve_best_practices_passages(
        input: TemporalBestPracticesReviewInput,
    ) -> list[BestPracticesPassage]:
        return [
            BestPracticesPassage(
                passage_id="passage-001",
                text="Keep workflows deterministic.",
                relevance_score=0.99,
            )
        ]

    @activity.defn(name="run_best_practices_review")
    async def run_best_practices_review(
        input: RunBestPracticesReviewActivityInput,
    ) -> BestPracticesReviewOutput:
        counters["review"] += 1
        return BestPracticesReviewOutput(
            retrieved_passages=input.passages,
            violations=[],
            verdict="pass",
            artifact_uri=f"file:///tmp/review-{counters['review']}.json",
        )

    @activity.defn(name="run_supervisor_decision")
    async def run_supervisor_decision(input: SupervisorDecisionInput) -> SupervisorDecision:
        counters["supervisor"] += 1
        action = supervisor_actions[min(counters["supervisor"] - 1, len(supervisor_actions) - 1)]
        questions = ["Please clarify the approval behavior."] if action == "ask_human" else []
        reason = None
        if action == "revise_from_draft":
            reason = "Tighten the draft wording."
        elif action == "revise_from_extraction":
            reason = "Source understanding is incomplete."
        return SupervisorDecision(
            action=action,
            reason=reason,
            questions=questions,
            artifact_uri=f"file:///tmp/supervisor-{counters['supervisor']}.json",
        )

    @activity.defn(name="persist_final_outputs")
    async def persist_final_outputs(
        input: PersistFinalOutputsInput,
    ) -> PersistFinalOutputsOutput:
        counters["persist"] += 1
        return PersistFinalOutputsOutput(
            artifact_uris={"spec_md": f"file:///tmp/final-{counters['persist']}.md"},
            output_directory=input.output_directory,
        )

    return [
        acquire_content,
        run_structured_extraction,
        run_intent_classification,
        run_extraction_critic,
        run_draft_spec,
        retrieve_best_practices_passages,
        run_best_practices_review,
        run_supervisor_decision,
        persist_final_outputs,
    ], counters


async def wait_for_status(
    handle,
    predicate: Callable[[SpecBuilderStatus], bool],
    *,
    attempts: int = 40,
) -> SpecBuilderStatus:
    """Poll a workflow query until the predicate matches."""
    latest_status: SpecBuilderStatus | None = None
    for _ in range(attempts):
        latest_status = await handle.query(SpecBuilderWorkflow.get_status)
        if predicate(latest_status):
            return latest_status
        await asyncio.sleep(0.1)
    raise AssertionError(f"Timed out waiting for matching workflow status: {latest_status}")


class TestSpecBuilderWorkflow:
    """Behavior tests for the parent spec builder workflow."""

    @pytest.fixture
    def task_queue(self) -> str:
        """Return a unique task queue for each test."""
        return SPEC_BUILDER_TASK_QUEUE

    def build_input(
        self,
        tmp_path,
        *,
        require_human_approval: bool = False,
        max_iterations_per_run: int = 4,
    ) -> SpecBuilderInput:
        """Build a workflow input fixture."""
        artifact_path = tmp_path / "input.md"
        artifact_path.write_text("# Input\n\nTemporal workflow notes.\n", encoding="utf-8")
        return SpecBuilderInput(
            job_id=f"job-{uuid.uuid4()}",
            artifacts=[
                ArtifactInput(
                    artifact_id="doc-001",
                    uri=str(artifact_path),
                    type="markdown",
                    title="Input Notes",
                )
            ],
            user_intent="Build a GUI-ready Temporal workflow spec.",
            output_targets=["markdown_spec", "structured_json"],
            output_directory=str(tmp_path / "output"),
            options=SpecBuilderOptions(
                require_human_approval=require_human_approval,
                continue_as_new_threshold=ContinueAsNewThreshold(
                    max_iterations_per_run=max_iterations_per_run,
                ),
            ),
        )

    @asynccontextmanager
    async def run_scenario(
        self,
        client: Client,
        task_queue: str,
        scenario: str,
        input_data: SpecBuilderInput,
    ):
        """Start a worker and workflow for one scenario."""
        activities, counters = build_mock_activities(scenario)
        async with Worker(
            client,
            task_queue=task_queue,
            workflows=[
                SpecBuilderWorkflow,
                AcquireContentWorkflow,
                StructuredExtractionWorkflow,
                IntentClassificationWorkflow,
                ExtractionCriticWorkflow,
                DraftSpecWorkflow,
                TemporalBestPracticesReviewWorkflow,
                SupervisorDecisionWorkflow,
            ],
            activities=activities,
        ):
            handle = await client.start_workflow(
                SpecBuilderWorkflow.run,
                input_data,
                id=f"spec-builder-{uuid.uuid4()}",
                task_queue=task_queue,
            )
            yield counters, handle

    @pytest.mark.asyncio
    async def test_should_complete_happy_path_when_supervisor_accepts_immediately(
        self,
        client: Client,
        task_queue: str,
        tmp_path,
    ) -> None:
        async with self.run_scenario(
            client,
            task_queue,
            "happy_path",
            self.build_input(tmp_path),
        ) as (counters, handle):
            result = await handle.result()

            assert result.status == "completed"
            assert counters["acquire"] == 1
            assert counters["draft"] == 1
            assert counters["persist"] == 1

    @pytest.mark.asyncio
    async def test_should_restart_from_draft_when_supervisor_requests_draft_revision(
        self,
        client: Client,
        task_queue: str,
        tmp_path,
    ) -> None:
        async with self.run_scenario(
            client,
            task_queue,
            "revise_from_draft",
            self.build_input(tmp_path),
        ) as (counters, handle):
            result = await handle.result()

            assert result.status == "completed"
            assert counters["acquire"] == 1
            assert counters["draft"] == 2

    @pytest.mark.asyncio
    async def test_should_restart_from_acquire_when_supervisor_requests_extraction_revision(
        self,
        client: Client,
        task_queue: str,
        tmp_path,
    ) -> None:
        async with self.run_scenario(
            client,
            task_queue,
            "revise_from_extraction",
            self.build_input(tmp_path),
        ) as (counters, handle):
            result = await handle.result()

            assert result.status == "completed"
            assert counters["acquire"] == 2
            assert counters["draft"] == 2

    @pytest.mark.asyncio
    async def test_should_pause_and_resume_when_supervisor_requests_human_clarification(
        self,
        client: Client,
        task_queue: str,
        tmp_path,
    ) -> None:
        async with self.run_scenario(
            client,
            task_queue,
            "ask_human",
            self.build_input(tmp_path),
        ) as (counters, handle):
            await wait_for_status(
                handle,
                lambda status: status.pending_human_action == "clarification",
            )

            await handle.signal(
                SpecBuilderWorkflow.submit_clarification,
                SubmitClarificationInput(
                    request_id="clarification-1",
                    clarification="Keep the approval flow explicit for the GUI.",
                ),
            )
            result = await handle.result()

            assert result.status == "completed"
            assert counters["acquire"] == 2

    @pytest.mark.asyncio
    async def test_should_wait_for_approval_when_human_approval_is_required(
        self,
        client: Client,
        task_queue: str,
        tmp_path,
    ) -> None:
        async with self.run_scenario(
            client,
            task_queue,
            "approval",
            self.build_input(tmp_path, require_human_approval=True),
        ) as (counters, handle):
            await wait_for_status(handle, lambda status: status.pending_human_action == "approval")

            await handle.signal(
                SpecBuilderWorkflow.approve_draft,
                ApproveDraftInput(request_id="approval-1", reviewer_note="Looks good"),
            )
            result = await handle.result()

            assert result.status == "completed"
            assert counters["persist"] == 1

    @pytest.mark.asyncio
    async def test_should_restart_from_draft_when_human_rejects_draft_with_draft_restart(
        self,
        client: Client,
        task_queue: str,
        tmp_path,
    ) -> None:
        async with self.run_scenario(
            client,
            task_queue,
            "reject_draft",
            self.build_input(tmp_path, require_human_approval=True),
        ) as (counters, handle):
            await wait_for_status(handle, lambda status: status.pending_human_action == "approval")
            await handle.signal(
                SpecBuilderWorkflow.reject_draft,
                RejectDraftInput(
                    request_id="reject-1",
                    reason="Please revise the draft wording.",
                    restart_point="draft",
                ),
            )
            await wait_for_status(handle, lambda status: status.pending_human_action == "approval")
            await handle.signal(
                SpecBuilderWorkflow.approve_draft,
                ApproveDraftInput(request_id="approval-2"),
            )
            result = await handle.result()

            assert result.status == "completed"
            assert counters["acquire"] == 1
            assert counters["draft"] == 2

    @pytest.mark.asyncio
    async def test_should_restart_from_acquire_when_human_rejects_draft_with_acquire_restart(
        self,
        client: Client,
        task_queue: str,
        tmp_path,
    ) -> None:
        async with self.run_scenario(
            client,
            task_queue,
            "reject_acquire",
            self.build_input(tmp_path, require_human_approval=True),
        ) as (counters, handle):
            await wait_for_status(handle, lambda status: status.pending_human_action == "approval")
            await handle.signal(
                SpecBuilderWorkflow.reject_draft,
                RejectDraftInput(
                    request_id="reject-1",
                    reason="Start over from acquisition.",
                    restart_point="acquire",
                ),
            )
            await wait_for_status(handle, lambda status: status.pending_human_action == "approval")
            await handle.signal(
                SpecBuilderWorkflow.approve_draft,
                ApproveDraftInput(request_id="approval-2"),
            )
            result = await handle.result()

            assert result.status == "completed"
            assert counters["acquire"] == 2

    @pytest.mark.asyncio
    async def test_should_continue_as_new_between_iterations_when_threshold_is_reached(
        self,
        client: Client,
        task_queue: str,
        tmp_path,
    ) -> None:
        input_data = self.build_input(tmp_path, max_iterations_per_run=1)
        async with self.run_scenario(
            client,
            task_queue,
            "continue_as_new",
            input_data,
        ) as (counters, handle):
            workflow_id = handle.id
            current_handle = client.get_workflow_handle(workflow_id)
            status = await wait_for_status(
                current_handle,
                lambda current: current.continuation_count >= 1,
            )
            result = SpecBuilderResult.model_validate(await current_handle.result())

            assert status.continuation_count >= 1
            assert result.status == "completed"
            assert counters["draft"] == 2

    @pytest.mark.asyncio
    async def test_should_cancel_when_user_cancels_while_waiting_for_human_input(
        self,
        client: Client,
        task_queue: str,
        tmp_path,
    ) -> None:
        async with self.run_scenario(
            client,
            task_queue,
            "cancel",
            self.build_input(tmp_path),
        ) as (counters, handle):
            await wait_for_status(
                handle,
                lambda status: status.pending_human_action == "clarification",
            )
            await handle.signal(
                SpecBuilderWorkflow.cancel_job,
                CancelJobInput(request_id="cancel-1", reason="User canceled from GUI."),
            )
            result = await handle.result()

            assert result.status == "cancelled"
            assert counters["persist"] == 0
