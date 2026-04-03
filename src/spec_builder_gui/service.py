"""Service layer for the local spec builder GUI backend."""

from __future__ import annotations

import asyncio
import mimetypes
import os
import re
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from temporalio.api.enums.v1 import WorkflowExecutionStatus
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter

from src.spec_builder_gui.api_models import (
    CreateJobRequest,
    CreateJobResponse,
    CreateRevisionRequest,
    CreateRevisionResponse,
    GuiJobStatus,
    JobDetail,
    JobSummary,
    ListJobsResponse,
    PickOutputDirectoryResponse,
    ReviewFeedbackEntry,
    StageName,
    StageState,
    StoredJobRecord,
    SubmitReviewRequest,
    UploadedArtifact,
    UploadArtifactResponse,
)
from src.spec_builder_gui.job_store import GuiJobStore, path_to_uri, uri_to_path
from src.workflows.spec_builder.custom_types import (
    ApproveDraftInput,
    ArtifactInput,
    BestPracticesViolation,
    CancelJobInput,
    DraftSpecOutput,
    FinalStatus,
    ForceFinalizeDraftInput,
    RejectDraftInput,
    SPEC_BUILDER_TASK_QUEUE,
    SpecBuilderInput,
    SpecBuilderOptions,
    SpecBuilderResult,
    SpecBuilderStatus,
    SubmitClarificationInput,
    TraceabilitySummary,
    WorkflowPhase,
)
from src.workflows.spec_builder.spec_builder_workflow import SpecBuilderWorkflow

MAX_FILE_COUNT = 20
MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024
MAX_TOTAL_UPLOAD_SIZE_BYTES = 200 * 1024 * 1024
STAGE_ORDER: tuple[StageName, ...] = (
    "acquire",
    "extract",
    "classify",
    "critic",
    "draft",
    "best_practices_review",
    "supervisor",
)
STAGE_INDEX = {stage: index for index, stage in enumerate(STAGE_ORDER)}
PHASE_TO_STAGE: dict[str, StageName] = {
    "acquire": "acquire",
    "extract": "extract",
    "classify": "classify",
    "critic": "critic",
    "draft": "draft",
    "best_practices_review": "best_practices_review",
    "supervisor": "supervisor",
}
STAGE_ARTIFACT_KEYS: dict[StageName, str] = {
    "acquire": "source_bundle",
    "extract": "structured_extraction",
    "classify": "intent_classification",
    "critic": "extraction_critic",
    "draft": "draft_markdown",
    "best_practices_review": "best_practices_review",
    "supervisor": "supervisor_decision",
}
ALLOWED_UPLOADS = {
    ".pdf": ("pdf", "document", {"application/pdf"}),
    ".png": ("image", "image", {"image/png"}),
    ".jpg": ("image", "image", {"image/jpeg"}),
    ".jpeg": ("image", "image", {"image/jpeg"}),
    ".webp": ("image", "image", {"image/webp"}),
}
ACTIVE_JOB_STATUSES = {"queued", "running", "waiting_for_human"}
CLOSED_JOB_STATUSES = {"completed", "failed", "cancelled"}


def utc_now() -> datetime:
    """Return the current UTC timestamp."""
    return datetime.now(tz=timezone.utc)


def slugify(value: str) -> str:
    """Convert arbitrary text into a filesystem-safe slug."""
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return normalized or "spec-builder"


def default_stage_states() -> list[StageState]:
    """Return the default stage timeline for a new job."""
    return [StageState(stage=stage, status="not_started") for stage in STAGE_ORDER]


class GuiServiceError(RuntimeError):
    """Known user-facing backend error."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(slots=True)
class StartedWorkflow:
    """Workflow start metadata returned by the Temporal gateway."""

    workflow_id: str
    run_id: str | None = None


@dataclass(slots=True)
class LiveWorkflowSnapshot:
    """Live or terminal workflow snapshot returned by the Temporal gateway."""

    workflow_status: int
    status: SpecBuilderStatus | None = None
    current_draft: DraftSpecOutput | None = None
    best_practices_violations: list[BestPracticesViolation] = field(default_factory=list)
    traceability_summary: TraceabilitySummary | None = None
    result: SpecBuilderResult | None = None

    @property
    def is_closed(self) -> bool:
        """Return whether the workflow is closed in Temporal."""
        return self.workflow_status != WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_RUNNING


class TemporalGateway(Protocol):
    """Protocol for Temporal-backed workflow operations used by the GUI service."""

    async def start_workflow(
        self,
        *,
        workflow_id: str,
        project_name: str,
        workflow_input: SpecBuilderInput,
    ) -> StartedWorkflow: ...

    async def fetch_snapshot(self, workflow_id: str) -> LiveWorkflowSnapshot: ...

    async def approve(self, workflow_id: str, *, reviewer_note: str | None) -> None: ...

    async def force_finalize(self, workflow_id: str, *, reason: str) -> None: ...

    async def reject(self, workflow_id: str, *, reason: str, restart_point: str) -> None: ...

    async def submit_clarification(self, workflow_id: str, *, clarification: str) -> None: ...

    async def cancel(self, workflow_id: str, *, reason: str) -> None: ...


class TemporalSpecBuilderGateway:
    """Temporal client adapter for GUI service operations."""

    def __init__(self, client: Client) -> None:
        self.client = client

    @classmethod
    async def connect(cls, target_host: str = "localhost:7233") -> TemporalSpecBuilderGateway:
        """Connect to Temporal and build a gateway instance."""
        client = await Client.connect(target_host, data_converter=pydantic_data_converter)
        return cls(client)

    async def start_workflow(
        self,
        *,
        workflow_id: str,
        project_name: str,
        workflow_input: SpecBuilderInput,
    ) -> StartedWorkflow:
        """Start a new spec builder workflow execution."""
        handle = await self.client.start_workflow(
            SpecBuilderWorkflow.run,
            workflow_input,
            id=workflow_id,
            task_queue=SPEC_BUILDER_TASK_QUEUE,
            result_type=SpecBuilderResult,
            static_summary=project_name,
        )
        return StartedWorkflow(
            workflow_id=handle.id,
            run_id=handle.first_execution_run_id or handle.run_id,
        )

    async def fetch_snapshot(self, workflow_id: str) -> LiveWorkflowSnapshot:
        """Fetch the current live or terminal workflow snapshot."""
        handle = self.client.get_workflow_handle(workflow_id, result_type=SpecBuilderResult)
        description = await handle.describe()
        workflow_status = description.raw_description.workflow_execution_info.status
        if workflow_status == WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_RUNNING:
            status = await handle.query(SpecBuilderWorkflow.get_status)
            current_draft = await handle.query(SpecBuilderWorkflow.get_current_draft)
            best_practices_violations = await handle.query(
                SpecBuilderWorkflow.get_best_practices_violations
            )
            traceability_summary = await handle.query(SpecBuilderWorkflow.get_traceability_summary)
            return LiveWorkflowSnapshot(
                workflow_status=workflow_status,
                status=status,
                current_draft=current_draft,
                best_practices_violations=best_practices_violations,
                traceability_summary=traceability_summary,
            )
        return LiveWorkflowSnapshot(
            workflow_status=workflow_status,
            result=SpecBuilderResult.model_validate(await handle.result()),
        )

    async def approve(self, workflow_id: str, *, reviewer_note: str | None) -> None:
        """Signal approval to the running workflow."""
        handle = self.client.get_workflow_handle(workflow_id)
        await handle.signal(
            SpecBuilderWorkflow.approve_draft,
            ApproveDraftInput(request_id=f"approve-{uuid.uuid4()}", reviewer_note=reviewer_note),
        )

    async def force_finalize(self, workflow_id: str, *, reason: str) -> None:
        """Signal force-finalization of the current reviewed draft."""
        handle = self.client.get_workflow_handle(workflow_id)
        await handle.signal(
            SpecBuilderWorkflow.force_finalize_draft,
            ForceFinalizeDraftInput(
                request_id=f"force-finalize-{uuid.uuid4()}",
                reason=reason,
            ),
        )

    async def reject(self, workflow_id: str, *, reason: str, restart_point: str) -> None:
        """Signal rejection to the running workflow."""
        handle = self.client.get_workflow_handle(workflow_id)
        await handle.signal(
            SpecBuilderWorkflow.reject_draft,
            RejectDraftInput(
                request_id=f"reject-{uuid.uuid4()}",
                reason=reason,
                restart_point=restart_point,
            ),
        )

    async def submit_clarification(self, workflow_id: str, *, clarification: str) -> None:
        """Signal clarification feedback to the running workflow."""
        handle = self.client.get_workflow_handle(workflow_id)
        await handle.signal(
            SpecBuilderWorkflow.submit_clarification,
            SubmitClarificationInput(
                request_id=f"clarify-{uuid.uuid4()}",
                clarification=clarification,
            ),
        )

    async def cancel(self, workflow_id: str, *, reason: str) -> None:
        """Signal cancellation to the running workflow."""
        handle = self.client.get_workflow_handle(workflow_id)
        await handle.signal(
            SpecBuilderWorkflow.cancel_job,
            CancelJobInput(request_id=f"cancel-{uuid.uuid4()}", reason=reason),
        )


class SpecBuilderGuiService:
    """Application service for the local spec builder GUI."""

    def __init__(self, *, job_store: GuiJobStore, temporal_gateway: TemporalGateway) -> None:
        self.job_store = job_store
        self.temporal_gateway = temporal_gateway

    def _artifact_type_for_upload(
        self,
        *,
        filename: str,
        mime_type: str | None,
    ) -> tuple[str, str, str]:
        extension = Path(filename).suffix.lower()
        if extension not in ALLOWED_UPLOADS:
            message = f"Unsupported file type for '{filename}'."
            raise GuiServiceError(message)
        kind, workflow_type, allowed_mime_types = ALLOWED_UPLOADS[extension]
        resolved_mime = (mime_type or mimetypes.guess_type(filename)[0] or "").lower()
        if resolved_mime and resolved_mime not in allowed_mime_types and resolved_mime != "application/octet-stream":
            message = f"Unsupported MIME type '{resolved_mime}' for '{filename}'."
            raise GuiServiceError(message)
        default_mime = next(iter(allowed_mime_types))
        return kind, workflow_type, resolved_mime or default_mime

    def _validate_output_directory(self, output_directory: str) -> str:
        path = Path(output_directory).expanduser().resolve()
        if not path.exists() or not path.is_dir():
            message = "Output directory must exist and be a directory."
            raise GuiServiceError(message)
        if not os.access(path, os.W_OK):
            message = f"Output directory is not writable: {path}"
            raise GuiServiceError(message)
        return str(path)

    def _sanitize_download_name(self, project_name: str) -> str:
        return f"{slugify(project_name)}-spec.md"

    def _map_closed_status(self, final_status: FinalStatus | None) -> GuiJobStatus:
        if final_status == "completed":
            return "completed"
        if final_status == "cancelled":
            return "cancelled"
        return "failed"

    def _download_artifact_uri(self, record: StoredJobRecord) -> str | None:
        if "approved_revision_spec_md" in record.artifact_uris:
            return record.artifact_uris["approved_revision_spec_md"]
        return record.artifact_uris.get("spec_md")

    def _refresh_stage_states(self, record: StoredJobRecord, *, synced_at: datetime) -> None:
        existing = {state.stage: state for state in record.stages}
        running_stage = PHASE_TO_STAGE.get(record.current_phase)
        failed_stage = running_stage if record.status in {"failed", "cancelled"} else None
        next_states: list[StageState] = []
        for index, stage in enumerate(STAGE_ORDER):
            previous = existing.get(stage, StageState(stage=stage, status="not_started"))
            artifact_present = STAGE_ARTIFACT_KEYS[stage] in record.artifact_uris
            if running_stage is not None and record.status in {"running", "queued"}:
                if index < STAGE_INDEX[running_stage]:
                    desired_status = "completed"
                elif stage == running_stage:
                    desired_status = "running"
                else:
                    desired_status = "not_started"
            elif stage == failed_stage:
                desired_status = "failed"
            elif artifact_present:
                desired_status = "completed"
            else:
                desired_status = "not_started"

            started_at = previous.started_at
            completed_at = previous.completed_at
            if desired_status == "not_started":
                started_at = None
                completed_at = None
            elif started_at is None:
                started_at = synced_at
            if desired_status in {"completed", "failed", "skipped"}:
                completed_at = completed_at or synced_at
            elif desired_status == "running":
                completed_at = None
            message = previous.message
            if desired_status == "failed":
                message = record.last_error or "Workflow ended before this stage completed."
            elif desired_status == "running":
                message = f"{stage.replace('_', ' ').title()} is in progress."
            elif desired_status == "not_started":
                message = None
            next_states.append(
                StageState(
                    stage=stage,
                    status=desired_status,
                    started_at=started_at,
                    completed_at=completed_at,
                    message=message,
                )
            )
        record.stages = next_states

    def _record_to_summary(self, record: StoredJobRecord) -> JobSummary:
        return JobSummary(
            job_id=record.job_id,
            workflow_id=record.workflow_id,
            run_id=record.run_id,
            project_name=record.project_name,
            status=record.status,
            current_phase=record.current_phase,
            iteration=record.iteration,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    def _record_to_detail(self, record: StoredJobRecord) -> JobDetail:
        has_markdown = (record.final_spec_markdown or record.current_draft_markdown) is not None
        has_high_risk_violations = any(
            violation.severity == "high" for violation in record.best_practices_violations
        )
        can_approve = (
            record.status == "waiting_for_human"
            and record.pending_human_action == "approval"
            and record.current_draft_markdown is not None
        )
        can_reject = can_approve
        can_provide_feedback = (
            record.status == "waiting_for_human"
            and record.pending_human_action == "clarification"
        )
        can_force_finalize = (
            record.status in ACTIVE_JOB_STATUSES
            and record.current_draft_markdown is not None
            and has_high_risk_violations
        )
        can_download = record.status in CLOSED_JOB_STATUSES and self._download_artifact_uri(record) is not None
        can_create_revision = (
            record.status in CLOSED_JOB_STATUSES and has_markdown
        )
        can_approve_revision_as_final = (
            record.parent_job_id is not None
            and record.status in CLOSED_JOB_STATUSES
            and has_markdown
            and "approved_revision_spec_md" not in record.artifact_uris
        )
        return JobDetail(
            job_id=record.job_id,
            workflow_id=record.workflow_id,
            run_id=record.run_id,
            project_name=record.project_name,
            status=record.status,
            current_phase=record.current_phase,
            iteration=record.iteration,
            created_at=record.created_at,
            updated_at=record.updated_at,
            output_directory=record.output_directory,
            artifacts=record.artifacts,
            stages=record.stages,
            open_questions=record.open_questions,
            warnings=record.warnings,
            current_draft_markdown=record.current_draft_markdown,
            final_spec_markdown=record.final_spec_markdown,
            review_feedback_history=record.review_feedback_history,
            best_practices_violations=record.best_practices_violations,
            current_draft_metadata=record.current_draft_metadata,
            traceability_summary=record.traceability_summary,
            pending_human_action=record.pending_human_action,
            final_workflow_status=record.final_workflow_status,
            artifact_uris=record.artifact_uris,
            can_approve=can_approve,
            can_reject=can_reject,
            can_provide_feedback=can_provide_feedback,
            can_force_finalize=can_force_finalize,
            can_download=can_download,
            can_create_revision=can_create_revision,
            can_approve_revision_as_final=can_approve_revision_as_final,
            parent_job_id=record.parent_job_id,
            revision_feedback=record.revision_feedback,
            created_from_version=record.created_from_version,
            last_synced_at=record.last_synced_at,
            last_error=record.last_error,
        )

    async def upload_artifact(
        self,
        *,
        filename: str,
        content: bytes,
        mime_type: str | None,
    ) -> UploadArtifactResponse:
        """Validate and persist one uploaded artifact."""
        if not filename:
            raise GuiServiceError("Uploaded file must include a filename.")
        if not content:
            raise GuiServiceError("Uploaded file must not be empty.")
        if len(content) > MAX_FILE_SIZE_BYTES:
            raise GuiServiceError("Uploaded file exceeds the 25 MiB per-file limit.")
        kind, workflow_type, resolved_mime = self._artifact_type_for_upload(
            filename=filename,
            mime_type=mime_type,
        )
        artifact_id = f"artifact-{uuid.uuid4().hex[:12]}"
        stored_file_path = self.job_store.artifact_file_path(artifact_id, filename)
        stored_file_path.write_bytes(content)
        artifact = UploadedArtifact(
            artifact_id=artifact_id,
            filename=filename,
            mime_type=resolved_mime,
            size_bytes=len(content),
            uploaded_at=utc_now(),
            kind=kind,
            file_uri=path_to_uri(stored_file_path),
            workflow_artifact_type=workflow_type,
            title=filename,
        )
        self.job_store.save_artifact(artifact)
        return UploadArtifactResponse(
            artifact_id=artifact.artifact_id,
            filename=artifact.filename,
            mime_type=artifact.mime_type,
            size_bytes=artifact.size_bytes,
            kind=artifact.kind,
        )

    async def create_job(self, request: CreateJobRequest) -> CreateJobResponse:
        """Create a workflow-backed job record and start the workflow."""
        project_name = request.project_name.strip()
        if not project_name:
            raise GuiServiceError("Project name is required.")
        if not request.artifact_ids:
            raise GuiServiceError("At least one uploaded artifact is required.")
        if len(request.artifact_ids) > MAX_FILE_COUNT:
            raise GuiServiceError("At most 20 artifacts can be submitted in one job.")
        artifacts = self.job_store.get_artifacts(request.artifact_ids)
        if len(artifacts) != len(request.artifact_ids):
            raise GuiServiceError("One or more artifact ids could not be found.", status_code=404)
        total_size = sum(artifact.size_bytes for artifact in artifacts)
        if total_size > MAX_TOTAL_UPLOAD_SIZE_BYTES:
            raise GuiServiceError("Combined upload size exceeds the 200 MiB limit.")
        output_directory = self._validate_output_directory(request.output_directory)
        job_id = f"job-{uuid.uuid4().hex[:12]}"
        workflow_id = f"spec-builder-ui-{job_id}"
        started_at = utc_now()
        workflow_input = SpecBuilderInput(
            job_id=job_id,
            artifacts=[
                ArtifactInput(
                    artifact_id=artifact.artifact_id,
                    uri=artifact.file_uri,
                    type=artifact.workflow_artifact_type,
                    mime_type=artifact.mime_type,
                    title=artifact.title,
                )
                for artifact in artifacts
            ],
            user_intent=request.user_intent,
            output_targets=request.output_targets,
            output_directory=output_directory,
            options=SpecBuilderOptions(require_human_approval=request.require_human_approval),
        )
        started = await self.temporal_gateway.start_workflow(
            workflow_id=workflow_id,
            project_name=project_name,
            workflow_input=workflow_input,
        )
        record = StoredJobRecord(
            job_id=job_id,
            workflow_id=started.workflow_id,
            run_id=started.run_id,
            project_name=project_name,
            created_at=started_at,
            updated_at=started_at,
            output_directory=output_directory,
            artifacts=artifacts,
            user_intent=request.user_intent,
            require_human_approval=request.require_human_approval,
            output_targets=request.output_targets,
            stages=default_stage_states(),
        )
        self.job_store.save_job(record)
        return CreateJobResponse(
            job_id=job_id,
            workflow_id=record.workflow_id,
            status=record.status,
            created_at=record.created_at,
        )

    async def refresh_job(self, job_id: str) -> StoredJobRecord:
        """Refresh one stored job record from Temporal live state."""
        record = self.job_store.get_job(job_id)
        if record is None:
            raise GuiServiceError("Job not found.", status_code=404)
        snapshot = await self.temporal_gateway.fetch_snapshot(record.workflow_id)
        synced_at = utc_now()
        record.last_synced_at = synced_at
        record.last_error = None
        if snapshot.result is not None:
            record.final_workflow_status = snapshot.result.status
            record.status = self._map_closed_status(snapshot.result.status)
            record.current_phase = (
                "completed"
                if snapshot.result.status == "completed"
                else "cancelled"
                if snapshot.result.status == "cancelled"
                else "failed"
            )
            record.iteration = record.iteration
            record.open_questions = snapshot.result.open_questions
            record.warnings = snapshot.result.warnings
            record.artifact_uris.update(snapshot.result.artifact_uris)
            record.pending_human_action = None
            record.best_practices_violations = (
                snapshot.result.best_practices_review.violations
                if snapshot.result.best_practices_review is not None
                else []
            )
            record.current_draft_markdown = snapshot.result.markdown_spec or record.current_draft_markdown
            if snapshot.result.status == "completed":
                record.final_spec_markdown = snapshot.result.markdown_spec
        elif snapshot.status is not None:
            record.status = (
                "waiting_for_human"
                if snapshot.status.pending_human_action is not None
                or snapshot.status.phase in {"waiting_for_human", "waiting_for_approval"}
                else "running"
            )
            record.current_phase = snapshot.status.phase
            record.iteration = snapshot.status.iteration
            record.open_questions = snapshot.status.open_questions
            record.warnings = snapshot.status.warnings
            record.pending_human_action = snapshot.status.pending_human_action
            record.artifact_uris.update(snapshot.status.artifact_uris)
            record.final_workflow_status = snapshot.status.final_status
            record.best_practices_violations = snapshot.best_practices_violations
            record.traceability_summary = snapshot.traceability_summary
            record.current_draft_metadata = snapshot.status.current_draft_metadata
            if snapshot.current_draft is not None:
                record.current_draft_markdown = snapshot.current_draft.markdown_spec
        record.updated_at = synced_at
        self._refresh_stage_states(record, synced_at=synced_at)
        self.job_store.save_job(record)
        return record

    async def list_jobs(
        self,
        *,
        status: GuiJobStatus | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> ListJobsResponse:
        """List jobs from the store, refreshing active ones first."""
        records = self.job_store.list_jobs()
        refreshed: list[StoredJobRecord] = []
        for record in records:
            if record.status in ACTIVE_JOB_STATUSES:
                try:
                    refreshed.append(await self.refresh_job(record.job_id))
                except Exception as exc:  # noqa: BLE001
                    record.last_error = str(exc)
                    self.job_store.save_job(record)
                    refreshed.append(record)
            else:
                refreshed.append(record)

        filtered = refreshed
        if status is not None:
            filtered = [record for record in filtered if record.status == status]
        if search:
            lowered = search.strip().lower()
            filtered = [
                record
                for record in filtered
                if lowered in record.project_name.lower() or lowered in record.job_id.lower()
            ]
        filtered.sort(key=lambda record: record.updated_at, reverse=True)
        start = max(page - 1, 0) * page_size
        end = start + page_size
        return ListJobsResponse(
            items=[self._record_to_summary(record) for record in filtered[start:end]],
            total=len(filtered),
        )

    async def get_job_detail(self, job_id: str) -> JobDetail:
        """Return one detailed job record, refreshing live state when needed."""
        record = self.job_store.get_job(job_id)
        if record is None:
            raise GuiServiceError("Job not found.", status_code=404)
        if record.status in ACTIVE_JOB_STATUSES:
            record = await self.refresh_job(job_id)
        return self._record_to_detail(record)

    def _append_review_entry(
        self,
        record: StoredJobRecord,
        *,
        kind: str,
        text: str | None,
    ) -> None:
        record.review_feedback_history.append(
            ReviewFeedbackEntry(
                id=f"review-{uuid.uuid4().hex[:12]}",
                created_at=utc_now(),
                author="local-user",
                kind=kind,
                text=text,
            )
        )
        record.updated_at = utc_now()

    async def submit_review(self, job_id: str, request: SubmitReviewRequest) -> JobDetail:
        """Apply a review action to a running job."""
        record = self.job_store.get_job(job_id)
        if record is None:
            raise GuiServiceError("Job not found.", status_code=404)
        if record.status not in ACTIVE_JOB_STATUSES:
            raise GuiServiceError("Review actions are only available for active jobs.")
        record = await self.refresh_job(job_id)

        if request.action == "approve":
            if record.pending_human_action != "approval":
                raise GuiServiceError("This job is not waiting for approval.")
            await self.temporal_gateway.approve(record.workflow_id, reviewer_note=request.text)
            self._append_review_entry(record, kind="approval", text=request.text)
        elif request.action == "force_finalize":
            if record.current_draft_markdown is None:
                raise GuiServiceError("This job does not have a draft available to finalize.")
            if not any(
                violation.severity == "high" for violation in record.best_practices_violations
            ):
                raise GuiServiceError(
                    "Force-finalize is only available when high-risk policy violations are present."
                )
            if not request.text or not request.text.strip():
                raise GuiServiceError("Force-finalize requires a written justification.")
            reason = request.text.strip()
            await self.temporal_gateway.force_finalize(record.workflow_id, reason=reason)
            self._append_review_entry(record, kind="force_finalize", text=reason)
        elif request.action == "reject":
            if record.pending_human_action != "approval":
                raise GuiServiceError("This job is not waiting for approval.")
            if not request.text or not request.text.strip():
                raise GuiServiceError("Rejection feedback is required.")
            await self.temporal_gateway.reject(
                record.workflow_id,
                reason=request.text.strip(),
                restart_point="draft",
            )
            self._append_review_entry(record, kind="rejection", text=request.text.strip())
        elif request.action == "feedback":
            if record.pending_human_action != "clarification":
                raise GuiServiceError("This job is not waiting for clarification feedback.")
            if not request.text or not request.text.strip():
                raise GuiServiceError("Feedback text is required.")
            await self.temporal_gateway.submit_clarification(
                record.workflow_id,
                clarification=request.text.strip(),
            )
            self._append_review_entry(record, kind="feedback", text=request.text.strip())
        else:
            reason = request.text.strip() if request.text else "User canceled from GUI."
            await self.temporal_gateway.cancel(record.workflow_id, reason=reason)
            self._append_review_entry(record, kind="cancel", text=reason)

        self.job_store.save_job(record)
        try:
            record = await self.refresh_job(job_id)
        except Exception:  # noqa: BLE001
            pass
        return self._record_to_detail(record)

    def _create_text_artifact(
        self,
        *,
        filename: str,
        content: str,
        kind: str,
        workflow_artifact_type: str,
    ) -> UploadedArtifact:
        artifact_id = f"artifact-{uuid.uuid4().hex[:12]}"
        stored_file_path = self.job_store.artifact_file_path(artifact_id, filename)
        stored_file_path.write_text(content, encoding="utf-8")
        artifact = UploadedArtifact(
            artifact_id=artifact_id,
            filename=filename,
            mime_type="text/markdown" if kind == "markdown" else "text/plain",
            size_bytes=len(content.encode("utf-8")),
            uploaded_at=utc_now(),
            kind=kind,
            file_uri=path_to_uri(stored_file_path),
            workflow_artifact_type=workflow_artifact_type,
            title=filename,
        )
        self.job_store.save_artifact(artifact)
        return artifact

    async def create_revision(
        self,
        job_id: str,
        request: CreateRevisionRequest,
    ) -> CreateRevisionResponse:
        """Create a revision workflow from a prior completed job."""
        parent = self.job_store.get_job(job_id)
        if parent is None:
            raise GuiServiceError("Parent job not found.", status_code=404)
        if not request.feedback.strip():
            raise GuiServiceError("Revision feedback is required.")
        parent_spec = parent.final_spec_markdown or parent.current_draft_markdown
        if not parent_spec:
            raise GuiServiceError("The selected job does not have a spec available for revision.")

        revision_project_name = (request.project_name or f"{parent.project_name} revision").strip()
        base_output_directory = Path(parent.output_directory).expanduser().resolve()
        revision_output_directory = (
            base_output_directory / "revisions" / f"{slugify(revision_project_name)}-{uuid.uuid4().hex[:6]}"
        )
        revision_output_directory.mkdir(parents=True, exist_ok=True)

        revision_artifacts = [
            artifact
            for artifact in parent.artifacts
            if uri_to_path(artifact.file_uri).exists()
        ]
        revision_artifacts.append(
            self._create_text_artifact(
                filename="prior-spec.md",
                content=parent_spec,
                kind="markdown",
                workflow_artifact_type="markdown",
            )
        )
        revision_artifacts.append(
            self._create_text_artifact(
                filename="revision-feedback.txt",
                content=request.feedback.strip(),
                kind="notes",
                workflow_artifact_type="notes",
            )
        )

        create_job_request = CreateJobRequest(
            project_name=revision_project_name,
            output_directory=str(revision_output_directory),
            artifact_ids=[artifact.artifact_id for artifact in revision_artifacts],
            user_intent=parent.user_intent,
            require_human_approval=parent.require_human_approval,
            output_targets=parent.output_targets,
        )
        created = await self.create_job(create_job_request)
        revision_record = self.job_store.get_job(created.job_id)
        if revision_record is None:
            raise GuiServiceError("Revision job was created but could not be persisted.", status_code=500)
        revision_record.parent_job_id = parent.job_id
        revision_record.revision_feedback = request.feedback.strip()
        revision_record.created_from_version = (
            parent.current_draft_metadata.draft_version if parent.current_draft_metadata else None
        )
        self.job_store.save_job(revision_record)
        return CreateRevisionResponse(
            job_id=revision_record.job_id,
            workflow_id=revision_record.workflow_id,
            status=revision_record.status,
        )

    async def approve_revision_as_final(self, job_id: str) -> JobDetail:
        """Persist a closed revision's markdown into its configured output directory."""
        record = self.job_store.get_job(job_id)
        if record is None:
            raise GuiServiceError("Job not found.", status_code=404)
        if record.parent_job_id is None:
            raise GuiServiceError("Only revision jobs can be approved as final.")
        if record.status not in CLOSED_JOB_STATUSES:
            raise GuiServiceError("Revision approval is only available after the workflow has stopped.")

        markdown = record.final_spec_markdown or record.current_draft_markdown
        if not markdown:
            raise GuiServiceError("This revision does not have markdown content to finalize.")

        output_directory = Path(self._validate_output_directory(record.output_directory))
        spec_path = output_directory / "spec.md"
        spec_path.write_text(markdown, encoding="utf-8")
        spec_uri = path_to_uri(spec_path)
        record.final_spec_markdown = markdown
        record.artifact_uris["approved_revision_spec_md"] = spec_uri
        record.artifact_uris.setdefault("spec_md", spec_uri)
        self._append_review_entry(
            record,
            kind="approval",
            text=f"Approved revision as final and saved to {spec_path}.",
        )
        self.job_store.save_job(record)
        return self._record_to_detail(record)

    async def get_download_path(self, job_id: str) -> tuple[Path, str]:
        """Return the final markdown path and suggested filename for download."""
        record = self.job_store.get_job(job_id)
        if record is None:
            raise GuiServiceError("Job not found.", status_code=404)
        if record.status == "completed":
            record = await self.refresh_job(job_id) if record.status in ACTIVE_JOB_STATUSES else record
        download_uri = self._download_artifact_uri(record)
        if record.status not in CLOSED_JOB_STATUSES or download_uri is None:
            raise GuiServiceError("A final markdown spec is not available for this job.", status_code=404)
        return (
            uri_to_path(download_uri),
            self._sanitize_download_name(record.project_name),
        )

    async def pick_output_directory(self) -> PickOutputDirectoryResponse:
        """Open a native directory picker when supported by the local host."""
        if sys.platform != "darwin":
            return PickOutputDirectoryResponse(supported=False)
        command = [
            "osascript",
            "-e",
            'POSIX path of (choose folder with prompt "Choose a spec output directory")',
        ]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode == 0:
            return PickOutputDirectoryResponse(
                supported=True,
                path=stdout.decode("utf-8").strip(),
            )
        if b"User canceled" in stderr:
            return PickOutputDirectoryResponse(supported=True, cancelled=True)
        message = stderr.decode("utf-8").strip() or "Directory picker failed."
        raise GuiServiceError(message, status_code=500)
