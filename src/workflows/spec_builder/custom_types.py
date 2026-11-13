"""Typed models for the spec builder Temporal workflow."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SPEC_BUILDER_TASK_QUEUE = "spec-builder-task-queue"

ArtifactType = Literal["document", "diagram", "image", "slides", "markdown", "notes"]
OutputTarget = Literal["markdown_spec", "structured_json"]
RestartPoint = Literal["acquire", "extract", "draft", "human_pause"]
WorkflowPhase = Literal[
    "acquire",
    "extract",
    "classify",
    "critic",
    "draft",
    "best_practices_review",
    "supervisor",
    "waiting_for_human",
    "waiting_for_approval",
    "completed",
    "cancelled",
    "failed",
]
PendingHumanAction = Literal["clarification", "approval"]
CriticRecommendedAction = Literal["proceed", "reextract", "ask_human"]
PrimaryIntent = Literal[
    "implementation_spec",
    "architecture_spec",
    "workflow_spec",
    "api_spec",
    "test_plan",
]
TargetAudience = Literal["codex", "engineer", "mixed"]
ExpectedOutputFormat = Literal["markdown", "json"]
BestPracticesProfile = Literal["temporal_workflow"]
EntityType = Literal[
    "workflow",
    "activity",
    "state",
    "input",
    "output",
    "api",
    "loop",
    "constraint",
    "component",
]
RelationshipType = Literal[
    "calls",
    "updates",
    "contains",
    "iterates_over",
    "produces",
    "depends_on",
]
FactClassification = Literal["explicit", "inferred"]
TraceabilityClassification = Literal["explicit", "inferred", "proposed_extension"]
ReviewSeverity = Literal["high", "medium", "low"]
BestPracticesVerdict = Literal["pass", "pass_with_warnings", "revise"]
SupervisorAction = Literal[
    "accept",
    "revise_from_extraction",
    "revise_from_draft",
    "ask_human",
]
FinalStatus = Literal["completed", "needs_clarification", "failed", "cancelled"]


class StrictModel(BaseModel):
    """Shared strict Pydantic base model."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ArtifactInput(StrictModel):
    """User-provided artifact reference."""

    artifact_id: str
    uri: str
    type: ArtifactType
    mime_type: str | None = None
    title: str | None = None


class SpecBuilderOptions(StrictModel):
    """Workflow runtime options."""

    max_iterations: int = Field(default=4, ge=1)
    max_extraction_restarts: int = Field(default=2, ge=0)
    max_draft_restarts: int = Field(default=2, ge=0)
    require_human_approval: bool = False
    best_practices_corpus_uri: str | None = None


class VisualElement(StrictModel):
    """Extracted visual element from a diagram/image artifact."""

    kind: Literal["node", "edge", "label", "group"]
    id: str | None = None
    text: str | None = None
    from_id: str | None = Field(default=None, alias="from")
    to_id: str | None = Field(default=None, alias="to")


class AcquiredArtifact(StrictModel):
    """Normalized representation of one acquired artifact."""

    artifact_id: str
    type: ArtifactType
    extracted_text: str | None = None
    visual_elements: list[VisualElement] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)
    source_uri: str | None = None
    normalized_text_uri: str | None = None


class AcquisitionOutput(StrictModel):
    """Stage 1 output."""

    source_bundle_id: str
    artifacts: list[AcquiredArtifact]
    warnings: list[str] = Field(default_factory=list)
    source_bundle_uri: str | None = None


class StructuredEntity(StrictModel):
    """Extracted entity in the intermediate representation."""

    id: str
    type: EntityType
    name: str
    source_artifact_ids: list[str]


class StructuredRelationship(StrictModel):
    """Relationship between extracted entities."""

    type: RelationshipType
    from_id: str = Field(alias="from")
    to_id: str = Field(alias="to")
    source_artifact_ids: list[str]


class StructuredFact(StrictModel):
    """One extracted fact with traceability."""

    statement: str
    classification: FactClassification
    confidence: float = Field(ge=0.0, le=1.0)
    source_artifact_ids: list[str]


class StructuredExtractionOutput(StrictModel):
    """Stage 2 output."""

    entities: list[StructuredEntity]
    relationships: list[StructuredRelationship]
    facts: list[StructuredFact]
    ambiguities: list[str] = Field(default_factory=list)
    artifact_uri: str | None = None


class IntentClassificationOutput(StrictModel):
    """Stage 3 output."""

    primary_intent: PrimaryIntent
    target_audience: TargetAudience
    expected_output_formats: list[ExpectedOutputFormat]
    best_practices_profile: BestPracticesProfile = "temporal_workflow"
    confidence: float = Field(ge=0.0, le=1.0)
    artifact_uri: str | None = None


class CriticOutput(StrictModel):
    """Stage 4 output."""

    score: float = Field(ge=0.0, le=1.0)
    blocking_issues: list[str] = Field(default_factory=list)
    non_blocking_issues: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    recommended_action: CriticRecommendedAction
    artifact_uri: str | None = None


class StructuredSpecComponent(StrictModel):
    """High-level component in the generated spec."""

    name: str
    responsibilities: list[str]


class TraceabilityEntry(StrictModel):
    """Traceability entry in the generated spec."""

    claim: str
    classification: TraceabilityClassification
    source_artifact_ids: list[str]


class TraceabilitySummary(StrictModel):
    """Compact traceability summary exposed to clients."""

    entries: list[TraceabilityEntry] = Field(default_factory=list)
    uncovered_claims: list[str] = Field(default_factory=list)


class StructuredSpec(StrictModel):
    """Structured JSON companion to the markdown spec."""

    title: str
    objective: str
    scope: list[str]
    components: list[StructuredSpecComponent]
    execution_flow: list[str]
    data_contracts: list[dict[str, Any]] = Field(default_factory=list)
    error_handling: list[str] = Field(default_factory=list)
    observability: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    traceability: list[TraceabilityEntry] = Field(default_factory=list)


class DraftSpecOutput(StrictModel):
    """Stage 5 output."""

    markdown_spec: str
    structured_spec: StructuredSpec
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    summary: str | None = None
    draft_version: int = 1
    markdown_spec_uri: str | None = None
    structured_spec_uri: str | None = None


class BestPracticesPassage(StrictModel):
    """Retrieved best-practices passage."""

    passage_id: str
    text: str
    relevance_score: float = Field(ge=0.0)


class BestPracticesViolation(StrictModel):
    """Violation or warning detected during best-practices review."""

    severity: ReviewSeverity
    issue: str
    related_passage_id: str | None = None
    suggested_fix: str


class BestPracticesReviewOutput(StrictModel):
    """Stage 6 output."""

    retrieved_passages: list[BestPracticesPassage] = Field(default_factory=list)
    violations: list[BestPracticesViolation] = Field(default_factory=list)
    verdict: BestPracticesVerdict
    artifact_uri: str | None = None


class SupervisorDecision(StrictModel):
    """Stage 7 output."""

    action: SupervisorAction
    reason: str | None = None
    questions: list[str] = Field(default_factory=list)
    artifact_uri: str | None = None


class SpecBuilderResult(StrictModel):
    """Workflow result returned to clients."""

    job_id: str
    status: FinalStatus
    markdown_spec: str | None = None
    structured_spec: StructuredSpec | None = None
    best_practices_review: BestPracticesReviewOutput | None = None
    open_questions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    artifact_uris: dict[str, str] = Field(default_factory=dict)


class DraftMetadata(StrictModel):
    """Compact current draft metadata for GUI polling."""

    draft_version: int
    summary: str | None = None
    markdown_spec_uri: str | None = None
    structured_spec_uri: str | None = None
    open_questions_count: int = 0
    assumption_count: int = 0


class ClarificationRecord(StrictModel):
    """Human clarification persisted in workflow state."""

    request_id: str
    clarification: str
    artifact_refs: list[ArtifactInput] = Field(default_factory=list)
    submitted_at: datetime | None = None
    note_uri: str | None = None


class SpecBuilderState(StrictModel):
    """Durable workflow state."""

    job_id: str
    phase: WorkflowPhase = "acquire"
    iteration: int = 0
    iterations_in_current_run: int = 0
    continuation_count: int = 0
    extraction_restarts: int = 0
    draft_restarts: int = 0
    restart_point: RestartPoint = "acquire"
    pending_human_action: PendingHumanAction | None = None
    acquisition: AcquisitionOutput | None = None
    extraction: StructuredExtractionOutput | None = None
    intent: IntentClassificationOutput | None = None
    extraction_critic: CriticOutput | None = None
    draft: DraftSpecOutput | None = None
    best_practices_review: BestPracticesReviewOutput | None = None
    supervisor_decision: SupervisorDecision | None = None
    previous_extraction: StructuredExtractionOutput | None = None
    previous_draft: DraftSpecOutput | None = None
    previous_best_practices_review: BestPracticesReviewOutput | None = None
    revision_notes: list[str] = Field(default_factory=list)
    clarifications: list[ClarificationRecord] = Field(default_factory=list)
    last_applied_clarification_count: int = 0
    open_questions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    artifact_uris: dict[str, str] = Field(default_factory=dict)
    processed_request_ids: list[str] = Field(default_factory=list)
    approval_note: str | None = None
    force_finalize_requested: bool = False
    force_finalize_reason: str | None = None
    canceled: bool = False
    cancel_reason: str | None = None
    final_result: SpecBuilderResult | None = None


class SpecBuilderStatus(StrictModel):
    """Query response for current workflow status."""

    job_id: str
    phase: WorkflowPhase
    iteration: int
    continuation_count: int
    extraction_restarts: int
    draft_restarts: int
    restart_point: RestartPoint
    pending_human_action: PendingHumanAction | None = None
    open_questions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    artifact_uris: dict[str, str] = Field(default_factory=dict)
    current_draft_metadata: DraftMetadata | None = None
    current_supervisor_action: SupervisorAction | None = None
    final_status: FinalStatus | None = None


class SpecBuilderLiveState(StrictModel):
    """Atomic live snapshot returned by the workflow for GUI polling."""

    status: SpecBuilderStatus
    current_draft: DraftSpecOutput | None = None
    best_practices_violations: list[BestPracticesViolation] = Field(default_factory=list)
    traceability_summary: TraceabilitySummary | None = None


class SubmitClarificationInput(StrictModel):
    """Signal payload for clarification."""

    request_id: str
    clarification: str
    artifact_refs: list[ArtifactInput] = Field(default_factory=list)


class ApproveDraftInput(StrictModel):
    """Signal payload for human approval."""

    request_id: str
    reviewer_note: str | None = None


class ForceFinalizeDraftInput(StrictModel):
    """Signal payload for force-finalizing the current reviewed draft."""

    request_id: str
    reason: str


class RejectDraftInput(StrictModel):
    """Signal payload for human rejection."""

    request_id: str
    reason: str
    restart_point: Literal["draft", "extract", "acquire"] = "draft"


class CancelJobInput(StrictModel):
    """Signal payload for cancellation."""

    request_id: str
    reason: str


class AcquireContentInput(StrictModel):
    """Stage 1 activity input."""

    job_id: str
    artifacts: list[ArtifactInput]
    clarifications: list[ClarificationRecord] = Field(default_factory=list)


class StructuredExtractionInput(StrictModel):
    """Stage 2 activity input."""

    job_id: str
    acquisition: AcquisitionOutput
    clarifications: list[ClarificationRecord] = Field(default_factory=list)
    revision_notes: list[str] = Field(default_factory=list)
    previous_extraction: StructuredExtractionOutput | None = None


class IntentClassificationInput(StrictModel):
    """Stage 3 activity input."""

    job_id: str
    extraction: StructuredExtractionOutput
    user_intent: str | None = None


class ExtractionCriticInput(StrictModel):
    """Stage 4 activity input."""

    job_id: str
    extraction: StructuredExtractionOutput
    intent: IntentClassificationOutput


class DraftSpecInput(StrictModel):
    """Stage 5 activity input."""

    job_id: str
    extraction: StructuredExtractionOutput
    intent: IntentClassificationOutput
    clarifications: list[ClarificationRecord] = Field(default_factory=list)
    revision_notes: list[str] = Field(default_factory=list)
    previous_draft: DraftSpecOutput | None = None
    previous_best_practices_review: BestPracticesReviewOutput | None = None


class TemporalBestPracticesReviewInput(StrictModel):
    """Retained child workflow input for stage 6."""

    job_id: str
    draft: DraftSpecOutput
    corpus_uri: str | None = None


class RunBestPracticesReviewActivityInput(StrictModel):
    """Activity input for the review assessment after retrieval."""

    job_id: str
    draft: DraftSpecOutput
    passages: list[BestPracticesPassage] = Field(default_factory=list)


class SupervisorDecisionInput(StrictModel):
    """Stage 7 activity input."""

    job_id: str
    extraction_critic: CriticOutput
    draft: DraftSpecOutput
    best_practices_review: BestPracticesReviewOutput
    iteration: int


class PersistFinalOutputsInput(StrictModel):
    """Activity input for final artifact materialization."""

    job_id: str
    output_directory: str
    draft: DraftSpecOutput
    best_practices_review: BestPracticesReviewOutput | None = None
    open_questions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PersistFinalOutputsOutput(StrictModel):
    """Activity output for final artifact materialization."""

    artifact_uris: dict[str, str] = Field(default_factory=dict)
    output_directory: str


class SpecBuilderInput(StrictModel):
    """Public workflow input."""

    job_id: str
    artifacts: list[ArtifactInput]
    user_intent: str | None = None
    output_targets: list[OutputTarget]
    output_directory: str
    options: SpecBuilderOptions = Field(default_factory=SpecBuilderOptions)
    architecture_mode: bool = False
    resume_state: SpecBuilderState | None = None


SpecBuilderState.model_rebuild()
SpecBuilderInput.model_rebuild()
