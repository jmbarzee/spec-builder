"""Tests for the local spec builder GUI backend."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest
from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer
from temporalio.api.enums.v1 import WorkflowExecutionStatus

from src.spec_builder_gui.api_models import CreateJobRequest, StoredJobRecord
from src.spec_builder_gui.job_store import GuiJobStore, path_to_uri, uri_to_path
from src.spec_builder_gui.server import create_app
from src.spec_builder_gui.service import LiveWorkflowSnapshot, SpecBuilderGuiService, StartedWorkflow
from src.workflows.spec_builder.custom_types import (
    BestPracticesReviewOutput,
    BestPracticesViolation,
    DraftMetadata,
    DraftSpecOutput,
    SpecBuilderInput,
    SpecBuilderResult,
    SpecBuilderStatus,
    StructuredSpec,
    StructuredSpecComponent,
    TraceabilityEntry,
    TraceabilitySummary,
)


def build_draft() -> DraftSpecOutput:
    """Build a compact draft fixture for GUI tests."""
    return DraftSpecOutput(
        markdown_spec="# Spec\n\nThis draft is ready for review.\n",
        structured_spec=StructuredSpec(
            title="Spec",
            objective="Ship a local GUI.",
            scope=["Create a frontend and backend bridge."],
            components=[
                StructuredSpecComponent(
                    name="GUI Backend",
                    responsibilities=["Bridge the browser and Temporal."],
                )
            ],
            execution_flow=["Upload", "Draft", "Review"],
            data_contracts=[{"name": "SpecBuilderInput"}],
            error_handling=["Return clear validation messages."],
            observability=["Show workflow phase in the UI."],
            assumptions=["The app runs locally."],
            open_questions=["Should approval be required?"],
            traceability=[
                TraceabilityEntry(
                    claim="The app runs locally.",
                    classification="explicit",
                    source_artifact_ids=["artifact-001"],
                )
            ],
        ),
        assumptions=["The app runs locally."],
        open_questions=["Should approval be required?"],
        summary="Frontend-backed draft.",
        draft_version=2,
        markdown_spec_uri="file:///tmp/spec.md",
        structured_spec_uri="file:///tmp/spec.json",
    )


def build_running_snapshot(
    job_id: str,
    *,
    phase: str = "draft",
    pending_human_action: str | None = None,
    best_practices_violations: list[BestPracticesViolation] | None = None,
) -> LiveWorkflowSnapshot:
    """Build a running workflow snapshot for GUI tests."""
    draft = build_draft()
    return LiveWorkflowSnapshot(
        workflow_status=WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_RUNNING,
        status=SpecBuilderStatus(
            job_id=job_id,
            phase=phase,
            iteration=1,
            continuation_count=0,
            extraction_restarts=0,
            draft_restarts=0,
            restart_point="draft" if phase == "draft" else "acquire",
            pending_human_action=pending_human_action,
            open_questions=["Should approval be required?"],
            warnings=[],
            artifact_uris={
                "source_bundle": "file:///tmp/source-bundle.json",
                "structured_extraction": "file:///tmp/extraction.json",
                "intent_classification": "file:///tmp/intent.json",
                "extraction_critic": "file:///tmp/critic.json",
                "draft_markdown": "file:///tmp/spec.md",
            },
            current_draft_metadata=DraftMetadata(
                draft_version=draft.draft_version,
                summary=draft.summary,
                markdown_spec_uri=draft.markdown_spec_uri,
                structured_spec_uri=draft.structured_spec_uri,
                open_questions_count=len(draft.open_questions),
                assumption_count=len(draft.assumptions),
            ),
            current_supervisor_action=None,
            final_status=None,
        ),
        current_draft=draft,
        best_practices_violations=best_practices_violations
        or [
            BestPracticesViolation(
                severity="medium",
                issue="Add a clearer approval summary.",
                suggested_fix="Surface pending human action in the header.",
            )
        ],
        traceability_summary=TraceabilitySummary(
            entries=draft.structured_spec.traceability,
            uncovered_claims=[],
        ),
    )


def build_completed_snapshot(job_id: str, spec_path: Path) -> LiveWorkflowSnapshot:
    """Build a completed workflow snapshot for GUI tests."""
    draft = build_draft()
    review = BestPracticesReviewOutput(
        retrieved_passages=[],
        violations=[
            BestPracticesViolation(
                severity="low",
                issue="Optional refinement",
                suggested_fix="Polish the timeline copy.",
            )
        ],
        verdict="pass_with_warnings",
    )
    return LiveWorkflowSnapshot(
        workflow_status=WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_COMPLETED,
        result=SpecBuilderResult(
            job_id=job_id,
            status="completed",
            markdown_spec=draft.markdown_spec,
            structured_spec=draft.structured_spec,
            best_practices_review=review,
            open_questions=[],
            warnings=[],
            artifact_uris={"spec_md": path_to_uri(spec_path)},
        ),
    )


class FakeTemporalGateway:
    """Fake Temporal gateway for GUI backend tests."""

    def __init__(self) -> None:
        self.started_inputs: list[tuple[str, str, SpecBuilderInput]] = []
        self.snapshots: dict[str, LiveWorkflowSnapshot] = {}
        self.snapshot_errors: dict[str, Exception] = {}
        self.approve_calls: list[tuple[str, str | None]] = []
        self.force_finalize_calls: list[tuple[str, str]] = []
        self.reject_calls: list[tuple[str, str, str]] = []
        self.feedback_calls: list[tuple[str, str]] = []
        self.cancel_calls: list[tuple[str, str]] = []

    async def start_workflow(
        self,
        *,
        workflow_id: str,
        project_name: str,
        workflow_input: SpecBuilderInput,
    ) -> StartedWorkflow:
        self.started_inputs.append((workflow_id, project_name, workflow_input))
        return StartedWorkflow(workflow_id=workflow_id, run_id="run-001")

    async def fetch_snapshot(self, workflow_id: str) -> LiveWorkflowSnapshot:
        if workflow_id in self.snapshot_errors:
            raise self.snapshot_errors[workflow_id]
        return self.snapshots[workflow_id]

    async def approve(self, workflow_id: str, *, reviewer_note: str | None) -> None:
        self.approve_calls.append((workflow_id, reviewer_note))

    async def force_finalize(self, workflow_id: str, *, reason: str) -> None:
        self.force_finalize_calls.append((workflow_id, reason))

    async def reject(self, workflow_id: str, *, reason: str, restart_point: str) -> None:
        self.reject_calls.append((workflow_id, reason, restart_point))

    async def submit_clarification(self, workflow_id: str, *, clarification: str) -> None:
        self.feedback_calls.append((workflow_id, clarification))

    async def cancel(self, workflow_id: str, *, reason: str) -> None:
        self.cancel_calls.append((workflow_id, reason))


@asynccontextmanager
async def app_client(tmp_path: Path):
    """Create a test client around the GUI aiohttp app."""
    gateway = FakeTemporalGateway()
    store = GuiJobStore(tmp_path / "gui-store")
    service = SpecBuilderGuiService(job_store=store, temporal_gateway=gateway)
    app = create_app(service=service, frontend_dist_dir=tmp_path / "frontend-dist")
    async with TestServer(app) as server, TestClient(server) as client:
        yield client, service, gateway


async def upload_test_artifact(client: TestClient) -> dict[str, object]:
    """Upload one PDF artifact via the HTTP API."""
    form = FormData()
    form.add_field(
        "file",
        b"%PDF-local-test",
        filename="brief.pdf",
        content_type="application/pdf",
    )
    response = await client.post("/api/artifacts", data=form)
    assert response.status == 201
    return await response.json()


@pytest.mark.asyncio
async def test_post_artifacts_should_validate_and_persist_upload(tmp_path: Path) -> None:
    """Uploading an artifact should persist metadata and the copied file."""
    async with app_client(tmp_path) as (client, service, _gateway):
        payload = await upload_test_artifact(client)

        stored = service.job_store.get_artifact(str(payload["artifactId"]))

        assert payload["filename"] == "brief.pdf"
        assert payload["kind"] == "pdf"
        assert stored is not None
        assert uri_to_path(stored.file_uri).exists()


@pytest.mark.asyncio
async def test_post_jobs_should_start_workflow_with_expected_input(tmp_path: Path) -> None:
    """Creating a job should start the Temporal workflow with the uploaded artifacts."""
    output_directory = tmp_path / "output"
    output_directory.mkdir()
    async with app_client(tmp_path) as (client, _service, gateway):
        artifact = await upload_test_artifact(client)
        response = await client.post(
            "/api/jobs",
            json=CreateJobRequest(
                project_name="Spotify Sync",
                output_directory=str(output_directory),
                artifact_ids=[str(artifact["artifactId"])],
                user_intent="Generate a frontend-ready spec.",
            ).model_dump(by_alias=True),
        )
        payload = await response.json()

        assert response.status == 201
        assert payload["status"] == "queued"
        assert gateway.started_inputs
        workflow_id, project_name, workflow_input = gateway.started_inputs[0]
        assert workflow_id.startswith("spec-builder-ui-job-")
        assert project_name == "Spotify Sync"
        assert workflow_input.options.require_human_approval is True
        assert workflow_input.artifacts[0].mime_type == "application/pdf"


@pytest.mark.asyncio
async def test_get_jobs_and_detail_should_merge_live_temporal_state(tmp_path: Path) -> None:
    """List and detail endpoints should refresh active jobs from Temporal state."""
    output_directory = tmp_path / "output"
    output_directory.mkdir()
    async with app_client(tmp_path) as (client, service, gateway):
        artifact = await upload_test_artifact(client)
        created = await client.post(
            "/api/jobs",
            json=CreateJobRequest(
                project_name="Realtime Detail",
                output_directory=str(output_directory),
                artifact_ids=[str(artifact["artifactId"])],
            ).model_dump(by_alias=True),
        )
        created_payload = await created.json()
        job_id = created_payload["jobId"]
        record = service.job_store.get_job(job_id)
        assert record is not None
        gateway.snapshots[record.workflow_id] = build_running_snapshot(job_id, phase="draft")

        list_response = await client.get("/api/jobs")
        list_payload = await list_response.json()
        detail_response = await client.get(f"/api/jobs/{job_id}")
        detail_payload = await detail_response.json()

        assert list_response.status == 200
        assert list_payload["total"] == 1
        assert list_payload["items"][0]["status"] == "running"
        assert detail_response.status == 200
        assert detail_payload["status"] == "running"
        assert detail_payload["currentPhase"] == "draft"
        assert detail_payload["currentDraftMarkdown"].startswith("# Spec")


@pytest.mark.asyncio
async def test_get_job_detail_should_fall_back_to_stored_state_when_refresh_fails(
    tmp_path: Path,
) -> None:
    """Detail endpoint should return the last stored snapshot when Temporal refresh fails."""
    output_directory = tmp_path / "output"
    output_directory.mkdir()
    async with app_client(tmp_path) as (client, service, gateway):
        artifact = await upload_test_artifact(client)
        created = await client.post(
            "/api/jobs",
            json=CreateJobRequest(
                project_name="Fallback Detail",
                output_directory=str(output_directory),
                artifact_ids=[str(artifact["artifactId"])],
            ).model_dump(by_alias=True),
        )
        job_id = (await created.json())["jobId"]
        record = service.job_store.get_job(job_id)
        assert record is not None
        record.status = "running"
        record.current_phase = "draft"
        record.current_draft_markdown = "# Cached draft\n\nStill visible.\n"
        service.job_store.save_job(record)
        gateway.snapshot_errors[record.workflow_id] = RuntimeError("Temporal query unavailable")

        detail_response = await client.get(f"/api/jobs/{job_id}")
        detail_payload = await detail_response.json()

        assert detail_response.status == 200
        assert detail_payload["status"] == "running"
        assert detail_payload["currentPhase"] == "draft"
        assert detail_payload["currentDraftMarkdown"].startswith("# Cached draft")
        assert detail_payload["lastError"] == "Temporal query unavailable"


@pytest.mark.asyncio
async def test_post_review_should_route_approve_reject_feedback_and_cancel(tmp_path: Path) -> None:
    """Review endpoint should map UI actions to the correct workflow signals."""
    output_directory = tmp_path / "output"
    output_directory.mkdir()
    async with app_client(tmp_path) as (client, service, gateway):
        artifact = await upload_test_artifact(client)
        created = await client.post(
            "/api/jobs",
            json=CreateJobRequest(
                project_name="Review Controls",
                output_directory=str(output_directory),
                artifact_ids=[str(artifact["artifactId"])],
            ).model_dump(by_alias=True),
        )
        job_id = (await created.json())["jobId"]
        record = service.job_store.get_job(job_id)
        assert record is not None

        gateway.snapshots[record.workflow_id] = build_running_snapshot(
            job_id,
            phase="waiting_for_approval",
            pending_human_action="approval",
        )
        approve_response = await client.post(
            f"/api/jobs/{job_id}/review",
            json={"action": "approve", "text": "Looks good."},
        )
        gateway.snapshots[record.workflow_id] = build_running_snapshot(
            job_id,
            phase="supervisor",
            best_practices_violations=[
                BestPracticesViolation(
                    severity="high",
                    issue="Current draft still mixes workflow and activity side effects.",
                    suggested_fix="Move side effects to activities before approval.",
                )
            ],
        )
        force_finalize_response = await client.post(
            f"/api/jobs/{job_id}/review",
            json={
                "action": "force_finalize",
                "text": "Accept the known risk and finalize this draft for implementation.",
            },
        )
        gateway.snapshots[record.workflow_id] = build_running_snapshot(
            job_id,
            phase="waiting_for_approval",
            pending_human_action="approval",
        )
        reject_response = await client.post(
            f"/api/jobs/{job_id}/review",
            json={"action": "reject", "text": "Needs one more pass."},
        )

        gateway.snapshots[record.workflow_id] = build_running_snapshot(
            job_id,
            phase="waiting_for_human",
            pending_human_action="clarification",
        )
        feedback_response = await client.post(
            f"/api/jobs/{job_id}/review",
            json={"action": "feedback", "text": "Clarify the approval criteria."},
        )

        gateway.snapshots[record.workflow_id] = build_running_snapshot(job_id, phase="draft")
        invalid_feedback_response = await client.post(
            f"/api/jobs/{job_id}/review",
            json={"action": "feedback", "text": "This should fail."},
        )
        cancel_response = await client.post(
            f"/api/jobs/{job_id}/review",
            json={"action": "cancel", "text": "Stop this run."},
        )

        assert approve_response.status == 200
        assert force_finalize_response.status == 200
        assert reject_response.status == 200
        assert feedback_response.status == 200
        assert invalid_feedback_response.status == 400
        assert cancel_response.status == 200
        assert gateway.approve_calls == [(record.workflow_id, "Looks good.")]
        assert gateway.force_finalize_calls == [
            (
                record.workflow_id,
                "Accept the known risk and finalize this draft for implementation.",
            )
        ]
        assert gateway.reject_calls == [(record.workflow_id, "Needs one more pass.", "draft")]
        assert gateway.feedback_calls == [(record.workflow_id, "Clarify the approval criteria.")]
        assert gateway.cancel_calls == [(record.workflow_id, "Stop this run.")]


@pytest.mark.asyncio
async def test_post_revise_should_create_revision_job_with_parent_context(tmp_path: Path) -> None:
    """Revision endpoint should create a new workflow with prior spec and feedback context."""
    output_directory = tmp_path / "output"
    output_directory.mkdir()
    spec_path = output_directory / "spec.md"
    spec_path.write_text("# Final Spec\n", encoding="utf-8")
    async with app_client(tmp_path) as (client, service, gateway):
        artifact = await service.upload_artifact(
            filename="source.pdf",
            content=b"%PDF-source",
            mime_type="application/pdf",
        )
        parent = StoredJobRecord(
            job_id="job-parent",
            workflow_id="spec-builder-ui-job-parent",
            run_id="run-parent",
            project_name="Parent Spec",
            created_at=service.job_store.get_artifact(artifact.artifact_id).uploaded_at,
            updated_at=service.job_store.get_artifact(artifact.artifact_id).uploaded_at,
            output_directory=str(output_directory),
            artifacts=service.job_store.get_artifacts([artifact.artifact_id]),
            status="completed",
            current_phase="completed",
            iteration=2,
            current_draft_markdown="# Final Spec\n",
            final_spec_markdown="# Final Spec\n",
            artifact_uris={"spec_md": path_to_uri(spec_path)},
            stages=[],
        )
        service.job_store.save_job(parent)

        response = await client.post(
            "/api/jobs/job-parent/revise",
            json={"projectName": "Parent Spec Remix", "feedback": "Add stronger review UX."},
        )
        payload = await response.json()
        revision_record = service.job_store.get_job(payload["jobId"])

        assert response.status == 201
        assert revision_record is not None
        assert revision_record.parent_job_id == "job-parent"
        assert revision_record.revision_feedback == "Add stronger review UX."
        assert gateway.started_inputs[-1][2].artifacts[-2].type == "markdown"
        assert gateway.started_inputs[-1][2].artifacts[-1].type == "notes"


@pytest.mark.asyncio
async def test_post_approve_revision_should_save_markdown_to_revision_output_directory(
    tmp_path: Path,
) -> None:
    """Approving a closed revision should materialize spec.md in its configured output directory."""
    output_directory = tmp_path / "revision-output"
    output_directory.mkdir()
    async with app_client(tmp_path) as (client, service, _gateway):
        record = StoredJobRecord(
            job_id="job-revision",
            workflow_id="spec-builder-ui-job-revision",
            run_id="run-revision",
            project_name="Revision Spec",
            created_at=datetime.now(tz=timezone.utc),
            updated_at=datetime.now(tz=timezone.utc),
            output_directory=str(output_directory),
            artifacts=[],
            status="completed",
            current_phase="completed",
            iteration=1,
            current_draft_markdown="# Revised Spec\n\nApproved.\n",
            final_spec_markdown="# Revised Spec\n\nApproved.\n",
            stages=[],
            parent_job_id="job-parent",
        )
        service.job_store.save_job(record)

        response = await client.post("/api/jobs/job-revision/approve-revision")
        payload = await response.json()
        refreshed = service.job_store.get_job("job-revision")

        assert response.status == 200
        assert payload["canApproveRevisionAsFinal"] is False
        assert payload["canDownload"] is True
        assert refreshed is not None
        assert (output_directory / "spec.md").read_text(encoding="utf-8") == "# Revised Spec\n\nApproved.\n"
        assert "approved_revision_spec_md" in refreshed.artifact_uris
        assert refreshed.review_feedback_history[-1].kind == "approval"


@pytest.mark.asyncio
async def test_get_download_should_return_final_markdown_with_sanitized_filename(tmp_path: Path) -> None:
    """Download endpoint should stream the final markdown with a sanitized filename."""
    output_directory = tmp_path / "output"
    output_directory.mkdir()
    spec_path = output_directory / "final spec.md"
    spec_path.write_text("# Final Spec\n", encoding="utf-8")
    async with app_client(tmp_path) as (client, service, _gateway):
        record = StoredJobRecord(
            job_id="job-download",
            workflow_id="spec-builder-ui-job-download",
            run_id="run-download",
            project_name="Spotify Styled Spec",
            created_at=datetime.now(tz=timezone.utc),
            updated_at=datetime.now(tz=timezone.utc),
            output_directory=str(output_directory),
            artifacts=[],
            status="completed",
            current_phase="completed",
            iteration=1,
            final_spec_markdown="# Final Spec\n",
            artifact_uris={"spec_md": path_to_uri(spec_path)},
            stages=[],
        )
        service.job_store.save_job(record)

        response = await client.get("/api/jobs/job-download/download")
        body = await response.text()

        assert response.status == 200
        assert "# Final Spec" in body
        assert 'filename="spotify-styled-spec-spec.md"' in response.headers["Content-Disposition"]


@pytest.mark.asyncio
async def test_pick_output_directory_should_degrade_cleanly_when_unsupported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Directory picker endpoint should return a clean unsupported response on non-macOS hosts."""
    monkeypatch.setattr("src.spec_builder_gui.service.sys.platform", "linux")
    async with app_client(tmp_path) as (client, _service, _gateway):
        response = await client.post("/api/system/pick-output-directory")
        payload = await response.json()

        assert response.status == 200
        assert payload == {"supported": False, "cancelled": False, "path": None}
