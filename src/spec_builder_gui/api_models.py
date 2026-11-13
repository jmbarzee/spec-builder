"""Pydantic models for the local spec builder GUI backend."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.workflows.spec_builder.custom_types import (
    BestPracticesViolation,
    DraftMetadata,
    FinalStatus,
    OutputTarget,
    PendingHumanAction,
    TraceabilitySummary,
    WorkflowPhase,
)

GuiJobStatus = Literal[
    "queued",
    "running",
    "waiting_for_human",
    "completed",
    "failed",
    "cancelled",
]
UploadedArtifactKind = Literal["pdf", "image", "markdown", "notes"]
ReviewEntryKind = Literal[
    "feedback", "approval", "rejection", "force_finalize", "cancel", "tool_decision"
]
StageName = Literal[
    "acquire",
    "extract",
    "classify",
    "critic",
    "draft",
    "best_practices_review",
    "supervisor",
]
StageExecutionStatus = Literal["not_started", "running", "completed", "failed", "skipped"]
ReviewAction = Literal["approve", "reject", "feedback", "force_finalize", "cancel"]


def to_camel(value: str) -> str:
    """Convert snake_case field names to camelCase aliases."""
    left, *rest = value.split("_")
    return left + "".join(part.capitalize() for part in rest)


class ApiModel(BaseModel):
    """Shared API model base with camelCase JSON aliases."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        alias_generator=to_camel,
    )


class UploadedArtifact(ApiModel):
    """Persisted uploaded artifact metadata."""

    artifact_id: str
    filename: str
    mime_type: str
    size_bytes: int
    uploaded_at: datetime
    kind: UploadedArtifactKind
    file_uri: str
    workflow_artifact_type: str
    title: str | None = None


class ReviewFeedbackEntry(ApiModel):
    """One human review action persisted in the GUI job store."""

    id: str
    created_at: datetime
    author: str
    kind: ReviewEntryKind
    text: str | None = None


class PendingToolCall(ApiModel):
    """An agent tool call held for a human decision."""

    tool_call_id: str
    tool_name: str
    args: str  # JSON


class StageState(ApiModel):
    """Normalized stage timeline state for the frontend."""

    stage: StageName
    status: StageExecutionStatus
    started_at: datetime | None = None
    completed_at: datetime | None = None
    message: str | None = None


class JobSummary(ApiModel):
    """Summary view for one job in list responses."""

    job_id: str
    workflow_id: str
    run_id: str | None = None
    project_name: str
    status: GuiJobStatus
    current_phase: WorkflowPhase
    iteration: int
    architecture_mode: bool = False
    created_at: datetime
    updated_at: datetime


class JobDetail(ApiModel):
    """Detailed job view used by the frontend detail page."""

    job_id: str
    workflow_id: str
    run_id: str | None = None
    project_name: str
    status: GuiJobStatus
    current_phase: WorkflowPhase
    iteration: int
    architecture_mode: bool = False
    created_at: datetime
    updated_at: datetime
    output_directory: str
    artifacts: list[UploadedArtifact] = Field(default_factory=list)
    stages: list[StageState] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    current_draft_markdown: str | None = None
    final_spec_markdown: str | None = None
    review_feedback_history: list[ReviewFeedbackEntry] = Field(default_factory=list)
    best_practices_violations: list[BestPracticesViolation] = Field(default_factory=list)
    current_draft_metadata: DraftMetadata | None = None
    traceability_summary: TraceabilitySummary | None = None
    pending_human_action: PendingHumanAction | None = None
    pending_tool_calls: list[PendingToolCall] = Field(default_factory=list)
    final_workflow_status: FinalStatus | None = None
    artifact_uris: dict[str, str] = Field(default_factory=dict)
    can_approve: bool = False
    can_reject: bool = False
    can_provide_feedback: bool = False
    can_decide_tool_calls: bool = False
    can_force_finalize: bool = False
    can_download: bool = False
    can_create_revision: bool = False
    can_approve_revision_as_final: bool = False
    parent_job_id: str | None = None
    revision_feedback: str | None = None
    created_from_version: int | None = None
    last_synced_at: datetime | None = None
    last_error: str | None = None


class CreateJobRequest(ApiModel):
    """Request payload for job creation."""

    project_name: str
    output_directory: str
    artifact_ids: list[str]
    user_intent: str | None = None
    require_human_approval: bool = True
    architecture_mode: bool = False
    output_targets: list[OutputTarget] = Field(
        default_factory=lambda: ["markdown_spec", "structured_json"]
    )


class CreateJobResponse(ApiModel):
    """Response payload for job creation."""

    job_id: str
    workflow_id: str
    status: GuiJobStatus
    created_at: datetime


class UploadArtifactResponse(ApiModel):
    """Response payload for one uploaded artifact."""

    artifact_id: str
    filename: str
    mime_type: str
    size_bytes: int
    kind: UploadedArtifactKind


class ListJobsResponse(ApiModel):
    """Response payload for list jobs."""

    items: list[JobSummary] = Field(default_factory=list)
    total: int


class SubmitReviewRequest(ApiModel):
    """Request payload for job review actions."""

    action: ReviewAction
    text: str | None = None


class DecideToolCallsRequest(ApiModel):
    """Request payload deciding every pending tool call at once."""

    decisions: dict[str, bool]  # tool_call_id -> approved


class CreateRevisionRequest(ApiModel):
    """Request payload for revision creation."""

    project_name: str | None = None
    feedback: str


class CreateRevisionResponse(ApiModel):
    """Response payload for revision creation."""

    job_id: str
    workflow_id: str
    status: GuiJobStatus


class PickOutputDirectoryResponse(ApiModel):
    """Response payload for the optional local directory picker."""

    supported: bool
    cancelled: bool = False
    path: str | None = None


class SystemFeatures(ApiModel):
    """Optional capabilities this deployment offers."""

    architecture_mode: bool


class TwfDesign(ApiModel):
    """A job's `.twf` design: its source and the visualizer payload from `twf`."""

    source: str
    parse: dict[str, Any]
    graph: dict[str, Any]


class StoredJobRecord(ApiModel):
    """Filesystem-backed job record persisted by the GUI backend."""

    job_id: str
    workflow_id: str
    run_id: str | None = None
    project_name: str
    created_at: datetime
    updated_at: datetime
    output_directory: str
    artifacts: list[UploadedArtifact] = Field(default_factory=list)
    status: GuiJobStatus = "queued"
    current_phase: WorkflowPhase = "acquire"
    iteration: int = 0
    open_questions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    review_feedback_history: list[ReviewFeedbackEntry] = Field(default_factory=list)
    best_practices_violations: list[BestPracticesViolation] = Field(default_factory=list)
    current_draft_markdown: str | None = None
    final_spec_markdown: str | None = None
    current_draft_metadata: DraftMetadata | None = None
    traceability_summary: TraceabilitySummary | None = None
    pending_human_action: PendingHumanAction | None = None
    pending_tool_calls: list[PendingToolCall] = Field(default_factory=list)
    final_workflow_status: FinalStatus | None = None
    artifact_uris: dict[str, str] = Field(default_factory=dict)
    stages: list[StageState] = Field(default_factory=list)
    user_intent: str | None = None
    require_human_approval: bool = True
    architecture_mode: bool = False
    output_targets: list[OutputTarget] = Field(
        default_factory=lambda: ["markdown_spec", "structured_json"]
    )
    parent_job_id: str | None = None
    revision_feedback: str | None = None
    created_from_version: int | None = None
    last_synced_at: datetime | None = None
    last_error: str | None = None
