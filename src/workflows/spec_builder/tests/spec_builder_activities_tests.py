"""Tests for spec builder activities."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from temporalio.testing import ActivityEnvironment

from src.workflows.spec_builder.custom_types import (
    AcquireContentInput,
    AcquiredArtifact,
    AcquisitionOutput,
    ArtifactInput,
    BestPracticesPassage,
    BestPracticesReviewOutput,
    ClarificationRecord,
    CriticOutput,
    DraftSpecOutput,
    DraftSpecInput,
    ExtractionCriticInput,
    IntentClassificationInput,
    IntentClassificationOutput,
    PersistFinalOutputsInput,
    RunBestPracticesReviewActivityInput,
    StructuredSpec,
    StructuredSpecComponent,
    StructuredExtractionInput,
    StructuredExtractionOutput,
    StructuredEntity,
    StructuredFact,
    StructuredRelationship,
    SupervisorDecision,
    SupervisorDecisionInput,
    TemporalBestPracticesReviewInput,
    TraceabilityEntry,
)
from src.workflows.spec_builder import model_executor
from src.workflows.spec_builder.spec_builder_activities import (
    acquire_content,
    persist_final_outputs,
    retrieve_best_practices_passages,
    run_best_practices_review,
    run_draft_spec,
    run_extraction_critic,
    run_intent_classification,
    run_structured_extraction,
    run_supervisor_decision,
)


class FakeExecutor:
    """Fake model executor for activity tests."""

    async def generate_structured(self, **kwargs: object) -> AcquiredArtifact:
        """Return a deterministic multimodal acquisition result."""
        prompt = str(kwargs["prompt"])
        if "Normalize the attached artifact" in prompt:
            return AcquiredArtifact(
                artifact_id="diagram-001",
                type="diagram",
                extracted_text="diagram summary",
                confidence=0.73,
                warnings=["low-resolution image"],
            )
        raise AssertionError("Unexpected generate_structured invocation")

    def embed_texts(self, texts: list[str], *, task_type: str, title: str | None = None) -> list[list[float]]:
        """Return deterministic vectors that prefer determinism-related content."""
        _ = title
        vectors: list[list[float]] = []
        for text in texts:
            if "deterministic" in text.lower():
                vectors.append([1.0, 0.0])
            elif task_type == "RETRIEVAL_QUERY":
                vectors.append([1.0, 0.0])
            else:
                vectors.append([0.0, 1.0])
        return vectors


class StageExecutor:
    """Fake model executor for stage-specific activity tests."""

    async def generate_structured(self, **kwargs: object):
        """Return a deterministic model instance for each stage."""
        stage_name = kwargs["stage_name"]
        response_model = kwargs["response_model"]
        if stage_name == "structured_extraction":
            return response_model(
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
                ambiguities=["Clarify whether human approval is required."],
            )
        if stage_name == "intent_classification":
            return response_model(
                primary_intent="implementation_spec",
                target_audience="codex",
                expected_output_formats=["markdown", "json"],
                best_practices_profile="temporal_workflow",
                confidence=0.94,
            )
        if stage_name == "extraction_critic":
            return response_model(
                score=0.88,
                blocking_issues=[],
                non_blocking_issues=["Add more detail on approval states."],
                missing_information=["Clarify approval gating."],
                recommended_action="proceed",
            )
        if stage_name == "draft_spec":
            return build_draft()
        if stage_name == "best_practices_review":
            return response_model(
                violations=[
                    {
                        "severity": "medium",
                        "issue": "Add explicit parent close policy guidance.",
                        "suggested_fix": "Document `ParentClosePolicy.TERMINATE`.",
                    }
                ],
                verdict="pass_with_warnings",
            )
        if stage_name == "supervisor_decision":
            return response_model(
                action="revise_from_draft",
                reason="Address the Temporal review warning.",
                questions=[],
            )
        raise AssertionError(f"Unexpected stage: {stage_name}")


def build_draft() -> DraftSpecOutput:
    """Build a compact draft fixture."""
    return DraftSpecOutput(
        markdown_spec="# Spec\n\nThe workflow must be deterministic.\n",
        structured_spec=StructuredSpec(
            title="Spec",
            objective="Build a workflow spec.",
            scope=["Generate a markdown spec"],
            components=[StructuredSpecComponent(name="Parent Workflow", responsibilities=["Orchestrate"])],
            execution_flow=["Acquire", "Draft", "Review"],
            data_contracts=[{"name": "SpecBuilderInput"}],
            error_handling=["Retry transient failures."],
            observability=["Emit stage logs."],
            assumptions=["Artifacts are local files."],
            open_questions=["Do we need a manual approval gate?"],
            traceability=[
                TraceabilityEntry(
                    claim="The workflow must be deterministic.",
                    classification="explicit",
                    source_artifact_ids=["doc-001"],
                )
            ],
        ),
        assumptions=["Artifacts are local files."],
        open_questions=["Do we need a manual approval gate?"],
        summary="Implementation-ready workflow draft.",
        draft_version=1,
    )


def build_structured_extraction() -> StructuredExtractionOutput:
    """Build a compact extraction fixture."""
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
        ambiguities=["Clarify whether human approval is required."],
    )


class TestSpecBuilderActivities:
    """Behavior tests for spec builder activities."""

    @pytest.mark.asyncio
    async def test_acquire_content_should_route_text_and_image_inputs_and_capture_clarifications(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        Scenario: Mixed artifact acquisition
        Given a submission contains text and image artifacts plus a clarification note
        When acquisition runs
        Then the output should include normalized text, multimodal extraction, and clarification artifacts
        """
        notes_path = tmp_path / "notes.md"
        notes_path.write_text("# Notes\n\nTemporal workflow details.\n", encoding="utf-8")
        image_path = tmp_path / "diagram.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake-image-data")

        monkeypatch.setattr(
            "src.workflows.spec_builder.spec_builder_activities._load_executor",
            lambda: FakeExecutor(),
        )

        activity_environment = ActivityEnvironment()
        result = await activity_environment.run(
            acquire_content,
            AcquireContentInput(
                job_id="job-acquire",
                artifacts=[
                    ArtifactInput(
                        artifact_id="notes-001",
                        uri=str(notes_path),
                        type="markdown",
                        title="Notes",
                    ),
                    ArtifactInput(
                        artifact_id="diagram-001",
                        uri=str(image_path),
                        type="diagram",
                        title="Diagram",
                    ),
                ],
                clarifications=[
                    ClarificationRecord(
                        request_id="clarification-1",
                        clarification="The draft should optimize for a future GUI client.",
                    )
                ],
            ),
        )

        artifact_ids = {artifact.artifact_id for artifact in result.artifacts}
        assert "notes-001" in artifact_ids
        assert "diagram-001" in artifact_ids
        assert "clarification-clarification-1" in artifact_ids
        text_artifact = next(artifact for artifact in result.artifacts if artifact.artifact_id == "notes-001")
        diagram_artifact = next(
            artifact for artifact in result.artifacts if artifact.artifact_id == "diagram-001"
        )
        assert "Temporal workflow details" in str(text_artifact.extracted_text)
        assert diagram_artifact.visual_elements == []
        assert diagram_artifact.normalized_text_uri is not None
        assert result.source_bundle_uri is not None

    @pytest.mark.asyncio
    async def test_persist_final_outputs_should_materialize_files_to_requested_output_directory(
        self,
        tmp_path: Path,
    ) -> None:
        """
        Scenario: Final artifact materialization
        Given a completed draft and review output
        When final persistence runs
        Then the requested output directory should contain real files for GUI linking
        """
        output_dir = tmp_path / "out"
        activity_environment = ActivityEnvironment()

        result = await activity_environment.run(
            persist_final_outputs,
            PersistFinalOutputsInput(
                job_id="job-persist",
                output_directory=str(output_dir),
                draft=build_draft(),
                best_practices_review=BestPracticesReviewOutput(
                    retrieved_passages=[
                        BestPracticesPassage(
                            passage_id="passage-001",
                            text="Keep workflows deterministic.",
                            relevance_score=0.99,
                        )
                    ],
                    violations=[],
                    verdict="pass",
                ),
                open_questions=["Do we need a manual approval gate?"],
                warnings=["No issues"],
            ),
        )

        assert (output_dir / "spec.md").exists()
        assert (output_dir / "spec.json").exists()
        assert (output_dir / "traceability.json").exists()
        assert (output_dir / "issues.json").exists()
        assert result.artifact_uris["spec_md"].startswith("file://")

    @pytest.mark.asyncio
    async def test_retrieve_best_practices_passages_should_rank_and_cache_relevant_sections(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        Scenario: Best-practices retrieval
        Given a small best-practices corpus and a draft about determinism
        When passage retrieval runs
        Then the determinism section should rank highest and embeddings should be cached
        """
        corpus_path = tmp_path / "corpus.md"
        corpus_path.write_text(
            "# Determinism\n\nKeep workflow code deterministic.\n\n"
            "# Child Workflows\n\nUse child workflows only when the boundary is meaningful.\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "src.workflows.spec_builder.spec_builder_activities._load_executor",
            lambda: FakeExecutor(),
        )

        activity_environment = ActivityEnvironment()
        result = await activity_environment.run(
            retrieve_best_practices_passages,
            TemporalBestPracticesReviewInput(
                job_id="job-retrieval",
                draft=build_draft(),
                corpus_uri=str(corpus_path),
            ),
        )

        assert result[0].text.startswith("# Determinism")
        cache_files = list((Path(".spec_builder_runtime") / "job-retrieval" / "corpus").glob("*.embeddings.json"))
        assert cache_files

    @pytest.mark.asyncio
    async def test_model_backed_stage_activities_should_return_persisted_outputs_when_executor_succeeds(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        Scenario: Core model-backed stage execution
        Given a deterministic executor stands in for the model
        When extraction, intent classification, critic, and draft activities run
        Then each activity should return the expected typed output and persistence URI
        """
        monkeypatch.setattr(
            "src.workflows.spec_builder.spec_builder_activities._load_executor",
            lambda: StageExecutor(),
        )
        activity_environment = ActivityEnvironment()
        acquisition = AcquisitionOutput(
            source_bundle_id="bundle-1",
            artifacts=[
                AcquiredArtifact(
                    artifact_id="doc-001",
                    type="markdown",
                    extracted_text="Temporal workflow notes.",
                    confidence=1.0,
                    source_uri="file:///tmp/doc.md",
                    normalized_text_uri="file:///tmp/doc-normalized.md",
                )
            ],
            warnings=[],
            source_bundle_uri="file:///tmp/bundle.json",
        )

        extraction = await activity_environment.run(
            run_structured_extraction,
            StructuredExtractionInput(job_id="job-model-stages", acquisition=acquisition),
        )
        intent = await activity_environment.run(
            run_intent_classification,
            IntentClassificationInput(
                job_id="job-model-stages",
                extraction=extraction,
                user_intent="Generate a Temporal implementation spec.",
            ),
        )
        critic = await activity_environment.run(
            run_extraction_critic,
            ExtractionCriticInput(
                job_id="job-model-stages",
                extraction=extraction,
                intent=intent,
            ),
        )
        draft = await activity_environment.run(
            run_draft_spec,
            DraftSpecInput(
                job_id="job-model-stages",
                extraction=extraction,
                intent=intent,
            ),
        )

        assert extraction.artifact_uri is not None
        assert intent.artifact_uri is not None
        assert critic.artifact_uri is not None
        assert draft.markdown_spec_uri is not None
        assert draft.structured_spec_uri is not None

    @pytest.mark.asyncio
    async def test_run_draft_spec_should_include_revision_context_and_increment_draft_version(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        Scenario: Draft revision prompt construction
        Given a previous draft, review findings, clarifications, and revision notes
        When the draft activity runs another pass
        Then the prompt should include that revision context and the draft version should advance
        """

        class PromptCapturingExecutor:
            """Capture the draft prompt while returning a deterministic draft."""

            def __init__(self) -> None:
                self.prompt = ""

            async def generate_structured(self, **kwargs: object) -> DraftSpecOutput:
                self.prompt = str(kwargs["prompt"])
                return build_draft()

        executor = PromptCapturingExecutor()
        monkeypatch.setattr(
            "src.workflows.spec_builder.spec_builder_activities._load_executor",
            lambda: executor,
        )
        activity_environment = ActivityEnvironment()
        previous_draft = build_draft().model_copy(update={"draft_version": 4})
        review = BestPracticesReviewOutput(
            retrieved_passages=[],
            violations=[
                {
                    "severity": "high",
                    "issue": "Move side effects into an activity.",
                    "suggested_fix": "Do not call external services from workflow code.",
                }
            ],
            verdict="revise",
        )

        draft = await activity_environment.run(
            run_draft_spec,
            DraftSpecInput(
                job_id="job-draft-revision",
                extraction=build_structured_extraction(),
                intent=IntentClassificationOutput(
                    primary_intent="implementation_spec",
                    target_audience="codex",
                    expected_output_formats=["markdown", "json"],
                    best_practices_profile="temporal_workflow",
                    confidence=0.94,
                ),
                clarifications=[
                    ClarificationRecord(
                        request_id="clarification-1",
                        clarification="Keep the approval gate explicit in the final draft.",
                    )
                ],
                revision_notes=["Tighten the timeout guidance."],
                previous_draft=previous_draft,
                previous_best_practices_review=review,
            ),
        )

        assert "Keep the approval gate explicit" in executor.prompt
        assert "Move side effects into an activity." in executor.prompt
        assert "Tighten the timeout guidance." in executor.prompt
        assert draft.draft_version == 5

    @pytest.mark.asyncio
    async def test_stage_should_run_on_the_provider_its_setting_names_when_others_keep_the_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        Scenario: One stage on a second provider
        Given only the intent-classification setting names an Anthropic model
        When the intent and critic activities run against a stubbed model
        Then intent should request the Anthropic spec, the critic its Gemini default,
        and the parsed output should reach the activity result
        """
        requested: list[str] = []

        def respond(_: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            properties = info.output_tools[0].parameters_json_schema["properties"]
            if "primary_intent" in properties:
                payload = IntentClassificationOutput(
                    primary_intent="implementation_spec",
                    target_audience="codex",
                    expected_output_formats=["markdown"],
                    best_practices_profile="temporal_workflow",
                    confidence=0.9,
                )
            else:
                payload = CriticOutput(
                    score=0.9,
                    blocking_issues=[],
                    non_blocking_issues=[],
                    missing_information=[],
                    recommended_action="proceed",
                )
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, payload.model_dump())])

        def build(spec: str) -> FunctionModel:
            requested.append(spec)
            return FunctionModel(respond)

        monkeypatch.setenv("SPEC_BUILDER_INTENT_CLASSIFICATION_MODEL", "anthropic:claude-sonnet-4-5")
        monkeypatch.setattr(model_executor, "_build_model", build)
        activity_environment = ActivityEnvironment()
        extraction = build_structured_extraction()

        intent = await activity_environment.run(
            run_intent_classification,
            IntentClassificationInput(job_id="job-provider", extraction=extraction, user_intent="Spec it."),
        )
        await activity_environment.run(
            run_extraction_critic,
            ExtractionCriticInput(job_id="job-provider", extraction=extraction, intent=intent),
        )

        assert requested == ["anthropic:claude-sonnet-4-5", "google-gla:gemini-3.1-pro-preview"]
        assert intent.primary_intent == "implementation_spec"

    @pytest.mark.asyncio
    async def test_review_and_supervisor_activities_should_return_structured_decisions_when_executor_succeeds(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        Scenario: Review and supervisor evaluation
        Given a deterministic executor stands in for the model
        When best-practices review and supervisor decision activities run
        Then they should return typed outputs with persisted URIs
        """
        monkeypatch.setattr(
            "src.workflows.spec_builder.spec_builder_activities._load_executor",
            lambda: StageExecutor(),
        )
        activity_environment = ActivityEnvironment()
        draft = build_draft()
        review = await activity_environment.run(
            run_best_practices_review,
            RunBestPracticesReviewActivityInput(
                job_id="job-review",
                draft=draft,
                passages=[
                    BestPracticesPassage(
                        passage_id="passage-001",
                        text="Use explicit parent close policies.",
                        relevance_score=0.92,
                    )
                ],
            ),
        )
        decision = await activity_environment.run(
            run_supervisor_decision,
            SupervisorDecisionInput(
                job_id="job-review",
                extraction_critic=CriticOutput(
                    score=0.9,
                    blocking_issues=[],
                    non_blocking_issues=[],
                    missing_information=[],
                    recommended_action="proceed",
                ),
                draft=draft,
                best_practices_review=review,
                iteration=0,
            ),
        )

        assert review.verdict == "pass_with_warnings"
        assert review.artifact_uri is not None
        assert decision.action == "accept"
        assert decision.artifact_uri is not None
