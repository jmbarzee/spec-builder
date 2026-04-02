"""Parent workflow for the GUI-ready Temporal spec builder."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from typing import Any

from temporalio import workflow
from temporalio.workflow import ParentClosePolicy

HUMAN_RESPONSE_TIMEOUT = timedelta(hours=24)

with workflow.unsafe.imports_passed_through():
    from temporalio.client import Client  # noqa: PLC0415
    from temporalio.contrib.pydantic import pydantic_data_converter  # noqa: PLC0415

    from src.workflows.spec_builder.acquire_content_workflow import AcquireContentWorkflow
    from src.workflows.spec_builder.custom_types import (
        AcquireContentInput,
        ApproveDraftInput,
        ArtifactInput,
        BestPracticesViolation,
        CancelJobInput,
        ClarificationRecord,
        DraftMetadata,
        DraftSpecInput,
        DraftSpecOutput,
        ExtractionCriticInput,
        IntentClassificationInput,
        PersistFinalOutputsInput,
        RejectDraftInput,
        SPEC_BUILDER_TASK_QUEUE,
        SpecBuilderInput,
        SpecBuilderResult,
        SpecBuilderState,
        SpecBuilderStatus,
        StructuredExtractionInput,
        SubmitClarificationInput,
        SupervisorDecisionInput,
        TemporalBestPracticesReviewInput,
        TraceabilitySummary,
    )
    from src.workflows.spec_builder.draft_spec_workflow import DraftSpecWorkflow
    from src.workflows.spec_builder.extraction_critic_workflow import ExtractionCriticWorkflow
    from src.workflows.spec_builder.intent_classification_workflow import (
        IntentClassificationWorkflow,
    )
    from src.workflows.spec_builder.spec_builder_activities import persist_final_outputs
    from src.workflows.spec_builder.structured_extraction_workflow import (
        StructuredExtractionWorkflow,
    )
    from src.workflows.spec_builder.supervisor_decision_workflow import (
        SupervisorDecisionWorkflow,
    )
    from src.workflows.spec_builder.temporal_best_practices_review_workflow import (
        TemporalBestPracticesReviewWorkflow,
    )


@workflow.defn
class SpecBuilderWorkflow:
    """Durable fixed-pipeline spec builder workflow."""

    def __init__(self) -> None:
        self.state: SpecBuilderState | None = None
        self._processed_request_ids: set[str] = set()
        self._approval_granted = False
        self._approval_note: str | None = None
        self._rejection_payload: RejectDraftInput | None = None

    def _ensure_state(self) -> SpecBuilderState:
        if self.state is None:
            raise RuntimeError("Workflow state has not been initialized yet.")
        return self.state

    def _merge_questions(self, *question_groups: list[str]) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for group in question_groups:
            for question in group:
                if question and question not in seen:
                    seen.add(question)
                    merged.append(question)
        return merged

    def _seen_request(self, request_id: str) -> bool:
        return request_id in self._processed_request_ids

    def _record_request(self, request_id: str) -> None:
        state = self._ensure_state()
        self._processed_request_ids.add(request_id)
        state.processed_request_ids = sorted(self._processed_request_ids)

    def _current_draft_metadata(self) -> DraftMetadata | None:
        state = self._ensure_state()
        if state.draft is None:
            return None
        return DraftMetadata(
            draft_version=state.draft.draft_version,
            summary=state.draft.summary,
            markdown_spec_uri=state.draft.markdown_spec_uri,
            structured_spec_uri=state.draft.structured_spec_uri,
            open_questions_count=len(state.draft.open_questions),
            assumption_count=len(state.draft.assumptions),
        )

    def _traceability_summary(self) -> TraceabilitySummary | None:
        state = self._ensure_state()
        if state.draft is None:
            return None
        known_claims = {entry.claim for entry in state.draft.structured_spec.traceability}
        uncovered_claims = [
            question for question in state.draft.open_questions if question not in known_claims
        ]
        return TraceabilitySummary(
            entries=state.draft.structured_spec.traceability,
            uncovered_claims=uncovered_claims,
        )

    def _status(self) -> SpecBuilderStatus:
        state = self._ensure_state()
        return SpecBuilderStatus(
            job_id=state.job_id,
            phase=state.phase,
            iteration=state.iteration,
            continuation_count=state.continuation_count,
            extraction_restarts=state.extraction_restarts,
            draft_restarts=state.draft_restarts,
            restart_point=state.restart_point,
            pending_human_action=state.pending_human_action,
            open_questions=state.open_questions,
            warnings=state.warnings,
            artifact_uris=state.artifact_uris,
            current_draft_metadata=self._current_draft_metadata(),
            current_supervisor_action=(
                state.supervisor_decision.action if state.supervisor_decision else None
            ),
            final_status=state.final_result.status if state.final_result else None,
        )

    async def _execute_stage_child(
        self,
        run_method: Any,
        stage_input: object,
        stage_name: str,
    ) -> Any:
        workflow.logger.info(
            "Starting child stage %s for job %s",
            stage_name,
            self._ensure_state().job_id,
        )
        return await workflow.execute_child_workflow(
            run_method,
            stage_input,
            id=f"{workflow.info().workflow_id}-{stage_name}-{workflow.uuid4()}",
            task_queue=SPEC_BUILDER_TASK_QUEUE,
            parent_close_policy=ParentClosePolicy.TERMINATE,
        )

    @workflow.query
    def get_status(self) -> SpecBuilderStatus:
        """Return the current workflow status snapshot."""
        return self._status()

    @workflow.query
    def get_current_phase(self) -> str:
        """Return the current workflow phase name."""
        return self._ensure_state().phase

    @workflow.query
    def get_current_draft(self) -> DraftSpecOutput | None:
        """Return the latest draft when available."""
        return self._ensure_state().draft

    @workflow.query
    def get_open_questions(self) -> list[str]:
        """Return the current open question list."""
        return self._ensure_state().open_questions

    @workflow.query
    def get_best_practices_violations(self) -> list[BestPracticesViolation]:
        """Return the current best-practices violations."""
        state = self._ensure_state()
        if state.best_practices_review is None:
            return []
        return state.best_practices_review.violations

    @workflow.query
    def get_traceability_summary(self) -> TraceabilitySummary | None:
        """Return the traceability summary for the current draft."""
        return self._traceability_summary()

    @workflow.signal
    def submit_clarification(self, payload: SubmitClarificationInput) -> None:
        """Record clarification text and optional supplemental artifacts."""
        if self.state is None:
            workflow.logger.warning("Clarification received before state initialization.")
            return
        if self._seen_request(payload.request_id):
            return
        self._record_request(payload.request_id)
        self.state.clarifications.append(
            ClarificationRecord(
                request_id=payload.request_id,
                clarification=payload.clarification,
                artifact_refs=payload.artifact_refs,
                submitted_at=workflow.now(),
            )
        )

    @workflow.signal
    def approve_draft(self, payload: ApproveDraftInput) -> None:
        """Approve the current draft when human approval is pending."""
        if self.state is None:
            workflow.logger.warning("Approval received before state initialization.")
            return
        if self._seen_request(payload.request_id):
            return
        self._record_request(payload.request_id)
        if self.state.pending_human_action != "approval":
            self.state.warnings = self._merge_questions(
                self.state.warnings,
                ["Received approve_draft while no approval gate was active."],
            )
            return
        self._approval_granted = True
        self._approval_note = payload.reviewer_note

    @workflow.signal
    def reject_draft(self, payload: RejectDraftInput) -> None:
        """Reject the current draft when human approval is pending."""
        if self.state is None:
            workflow.logger.warning("Rejection received before state initialization.")
            return
        if self._seen_request(payload.request_id):
            return
        self._record_request(payload.request_id)
        if self.state.pending_human_action != "approval":
            self.state.warnings = self._merge_questions(
                self.state.warnings,
                ["Received reject_draft while no approval gate was active."],
            )
            return
        self._rejection_payload = payload

    @workflow.signal
    def cancel_job(self, payload: CancelJobInput) -> None:
        """Cancel the current submission."""
        if self.state is None:
            workflow.logger.warning("Cancellation received before state initialization.")
            return
        if self._seen_request(payload.request_id):
            return
        self._record_request(payload.request_id)
        self.state.canceled = True
        self.state.cancel_reason = payload.reason

    async def _wait_for_clarification(self, questions: list[str]) -> bool:
        state = self._ensure_state()
        state.phase = "waiting_for_human"
        state.pending_human_action = "clarification"
        state.open_questions = self._merge_questions(state.open_questions, questions)
        baseline = state.last_applied_clarification_count
        if len(state.clarifications) <= baseline and not state.canceled:
            try:
                await workflow.wait_condition(
                    lambda: self._ensure_state().canceled
                    or len(self._ensure_state().clarifications) > baseline,
                    timeout=HUMAN_RESPONSE_TIMEOUT,
                    timeout_summary="waiting for clarification",
                )
            except TimeoutError:
                return False
        if state.canceled:
            return False
        state.pending_human_action = None
        return True

    async def _wait_for_approval(self) -> str:
        state = self._ensure_state()
        state.phase = "waiting_for_approval"
        state.pending_human_action = "approval"
        if not self._approval_granted and self._rejection_payload is None and not state.canceled:
            try:
                await workflow.wait_condition(
                    lambda: self._approval_granted
                    or self._rejection_payload is not None
                    or self._ensure_state().canceled,
                    timeout=HUMAN_RESPONSE_TIMEOUT,
                    timeout_summary="waiting for approval",
                )
            except TimeoutError:
                return "timeout"
        if state.canceled:
            return "canceled"
        if self._approval_granted:
            state.pending_human_action = None
            state.approval_note = self._approval_note
            self._approval_granted = False
            self._approval_note = None
            return "approved"
        if self._rejection_payload is not None:
            return "rejected"
        return "timeout"

    async def _advance_iteration(self, input: SpecBuilderInput) -> None:
        state = self._ensure_state()
        state.iteration += 1
        state.iterations_in_current_run += 1
        threshold = input.options.continue_as_new_threshold

        should_continue = False
        if (
            threshold.max_iterations_per_run is not None
            and state.iterations_in_current_run >= threshold.max_iterations_per_run
        ):
            should_continue = True
        if (
            threshold.max_history_length is not None
            and workflow.info().get_current_history_length() >= threshold.max_history_length
        ):
            should_continue = True
        if workflow.info().is_continue_as_new_suggested():
            should_continue = True
        if not should_continue:
            return

        await workflow.wait_condition(workflow.all_handlers_finished)
        resumed_state = state.model_copy(
            deep=True,
            update={
                "iterations_in_current_run": 0,
                "continuation_count": state.continuation_count + 1,
            },
        )
        workflow.continue_as_new(input.model_copy(deep=True, update={"resume_state": resumed_state}))

    async def _complete(self, input: SpecBuilderInput) -> SpecBuilderResult:
        state = self._ensure_state()
        if state.draft is None:
            raise RuntimeError("Cannot complete workflow without a draft.")
        persisted = await workflow.execute_activity(
            persist_final_outputs,
            PersistFinalOutputsInput(
                job_id=input.job_id,
                output_directory=input.output_directory,
                draft=state.draft,
                best_practices_review=state.best_practices_review,
                open_questions=state.open_questions,
                warnings=state.warnings,
            ),
            start_to_close_timeout=timedelta(minutes=5),
        )
        result = SpecBuilderResult(
            job_id=input.job_id,
            status="completed",
            markdown_spec=state.draft.markdown_spec,
            structured_spec=state.draft.structured_spec,
            best_practices_review=state.best_practices_review,
            open_questions=state.open_questions,
            warnings=state.warnings,
            artifact_uris={**state.artifact_uris, **persisted.artifact_uris},
        )
        state.phase = "completed"
        state.final_result = result
        state.artifact_uris.update(persisted.artifact_uris)
        return result

    def _result_from_state(
        self,
        *,
        status: str,
        extra_warning: str | None = None,
    ) -> SpecBuilderResult:
        state = self._ensure_state()
        warnings = list(state.warnings)
        if extra_warning:
            warnings = self._merge_questions(warnings, [extra_warning])
        result = SpecBuilderResult(
            job_id=state.job_id,
            status=status,
            markdown_spec=state.draft.markdown_spec if state.draft else None,
            structured_spec=state.draft.structured_spec if state.draft else None,
            best_practices_review=state.best_practices_review,
            open_questions=state.open_questions,
            warnings=warnings,
            artifact_uris=state.artifact_uris,
        )
        state.final_result = result
        if status == "failed":
            state.phase = "failed"
        elif status == "cancelled":
            state.phase = "cancelled"
        else:
            state.phase = "completed"
        return result

    @workflow.run
    async def run(self, input: SpecBuilderInput) -> SpecBuilderResult:
        """Run the fixed spec-builder pipeline with bounded iteration."""
        self.state = (
            input.resume_state.model_copy(deep=True)
            if input.resume_state
            else SpecBuilderState(job_id=input.job_id)
        )
        self._processed_request_ids = set(self.state.processed_request_ids)
        if self.state.job_id != input.job_id:
            self.state.job_id = input.job_id

        try:
            while self.state.iteration < input.options.max_iterations:
                if self.state.canceled:
                    return self._result_from_state(
                        status="cancelled",
                        extra_warning=self.state.cancel_reason or "Submission canceled.",
                    )

                if self.state.restart_point == "acquire":
                    self.state.phase = "acquire"
                    self.state.acquisition = await self._execute_stage_child(
                        AcquireContentWorkflow.run,
                        AcquireContentInput(
                            job_id=input.job_id,
                            artifacts=input.artifacts,
                            clarifications=self.state.clarifications,
                        ),
                        "acquire",
                    )
                    self.state.last_applied_clarification_count = len(self.state.clarifications)
                    if self.state.acquisition.source_bundle_uri:
                        self.state.artifact_uris["source_bundle"] = self.state.acquisition.source_bundle_uri
                    self.state.warnings = self._merge_questions(
                        self.state.warnings,
                        self.state.acquisition.warnings,
                    )

                    self.state.phase = "extract"
                    self.state.extraction = await self._execute_stage_child(
                        StructuredExtractionWorkflow.run,
                        StructuredExtractionInput(
                            job_id=input.job_id,
                            acquisition=self.state.acquisition,
                        ),
                        "extract",
                    )
                    if self.state.extraction.artifact_uri:
                        self.state.artifact_uris["structured_extraction"] = self.state.extraction.artifact_uri

                    self.state.phase = "classify"
                    self.state.intent = await self._execute_stage_child(
                        IntentClassificationWorkflow.run,
                        IntentClassificationInput(
                            job_id=input.job_id,
                            extraction=self.state.extraction,
                            user_intent=input.user_intent,
                        ),
                        "classify",
                    )
                    if self.state.intent.artifact_uri:
                        self.state.artifact_uris["intent_classification"] = self.state.intent.artifact_uri

                self.state.phase = "critic"
                self.state.extraction_critic = await self._execute_stage_child(
                    ExtractionCriticWorkflow.run,
                    ExtractionCriticInput(
                        job_id=input.job_id,
                        extraction=self.state.extraction,
                        intent=self.state.intent,
                    ),
                    "critic",
                )
                if self.state.extraction_critic.artifact_uri:
                    self.state.artifact_uris["extraction_critic"] = self.state.extraction_critic.artifact_uri
                self.state.open_questions = self._merge_questions(
                    self.state.extraction_critic.missing_information,
                    self.state.extraction.ambiguities if self.state.extraction else [],
                )

                if self.state.extraction_critic.recommended_action == "reextract":
                    if self.state.extraction_restarts >= input.options.max_extraction_restarts:
                        clarified = await self._wait_for_clarification(self.state.open_questions)
                        if not clarified:
                            if self.state.canceled:
                                return self._result_from_state(
                                    status="cancelled",
                                    extra_warning=self.state.cancel_reason or "Submission canceled.",
                                )
                            return self._result_from_state(
                                status="needs_clarification",
                                extra_warning="Timed out waiting for clarification.",
                            )
                    else:
                        self.state.extraction_restarts += 1
                    self.state.restart_point = "acquire"
                    await self._advance_iteration(input)
                    continue

                if self.state.extraction_critic.recommended_action == "ask_human":
                    clarified = await self._wait_for_clarification(self.state.open_questions)
                    if not clarified:
                        if self.state.canceled:
                            return self._result_from_state(
                                status="cancelled",
                                extra_warning=self.state.cancel_reason or "Submission canceled.",
                            )
                        return self._result_from_state(
                            status="needs_clarification",
                            extra_warning="Timed out waiting for clarification.",
                        )
                    self.state.restart_point = "acquire"
                    await self._advance_iteration(input)
                    continue

                self.state.phase = "draft"
                self.state.draft = await self._execute_stage_child(
                    DraftSpecWorkflow.run,
                    DraftSpecInput(
                        job_id=input.job_id,
                        extraction=self.state.extraction,
                        intent=self.state.intent,
                    ),
                    "draft",
                )
                if self.state.draft.markdown_spec_uri:
                    self.state.artifact_uris["draft_markdown"] = self.state.draft.markdown_spec_uri
                if self.state.draft.structured_spec_uri:
                    self.state.artifact_uris["draft_structured"] = self.state.draft.structured_spec_uri
                self.state.open_questions = self._merge_questions(
                    self.state.open_questions,
                    self.state.draft.open_questions,
                )

                self.state.phase = "best_practices_review"
                self.state.best_practices_review = await self._execute_stage_child(
                    TemporalBestPracticesReviewWorkflow.run,
                    TemporalBestPracticesReviewInput(
                        job_id=input.job_id,
                        draft=self.state.draft,
                        corpus_uri=input.options.best_practices_corpus_uri,
                    ),
                    "best-practices-review",
                )
                if self.state.best_practices_review.artifact_uri:
                    self.state.artifact_uris["best_practices_review"] = (
                        self.state.best_practices_review.artifact_uri
                    )

                self.state.phase = "supervisor"
                self.state.supervisor_decision = await self._execute_stage_child(
                    SupervisorDecisionWorkflow.run,
                    SupervisorDecisionInput(
                        job_id=input.job_id,
                        extraction_critic=self.state.extraction_critic,
                        draft=self.state.draft,
                        best_practices_review=self.state.best_practices_review,
                        iteration=self.state.iteration,
                    ),
                    "supervisor",
                )
                if self.state.supervisor_decision.artifact_uri:
                    self.state.artifact_uris["supervisor_decision"] = (
                        self.state.supervisor_decision.artifact_uri
                    )

                if self.state.supervisor_decision.action == "accept":
                    if input.options.require_human_approval:
                        approval_outcome = await self._wait_for_approval()
                        if approval_outcome == "approved":
                            return await self._complete(input)
                        if approval_outcome == "timeout":
                            return self._result_from_state(
                                status="needs_clarification",
                                extra_warning="Timed out waiting for approval.",
                            )
                        if approval_outcome == "canceled":
                            return self._result_from_state(
                                status="cancelled",
                                extra_warning=self.state.cancel_reason or "Submission canceled.",
                            )
                        if self._rejection_payload is not None:
                            payload = self._rejection_payload
                            self._rejection_payload = None
                            self.state.pending_human_action = None
                            self.state.open_questions = self._merge_questions(
                                self.state.open_questions,
                                [payload.reason],
                            )
                            if payload.restart_point == "acquire":
                                self.state.extraction_restarts += 1
                                self.state.restart_point = "acquire"
                            else:
                                self.state.draft_restarts += 1
                                self.state.restart_point = "draft"
                            await self._advance_iteration(input)
                            continue
                    else:
                        return await self._complete(input)

                if self.state.supervisor_decision.action == "revise_from_extraction":
                    reason = self.state.supervisor_decision.reason or "Supervisor requested re-extraction."
                    if self.state.extraction_restarts >= input.options.max_extraction_restarts:
                        clarified = await self._wait_for_clarification([reason])
                        if not clarified:
                            if self.state.canceled:
                                return self._result_from_state(
                                    status="cancelled",
                                    extra_warning=self.state.cancel_reason or "Submission canceled.",
                                )
                            return self._result_from_state(
                                status="needs_clarification",
                                extra_warning="Timed out waiting for clarification.",
                            )
                    else:
                        self.state.extraction_restarts += 1
                    self.state.restart_point = "acquire"
                    self.state.open_questions = self._merge_questions(
                        self.state.open_questions,
                        [reason],
                    )
                elif self.state.supervisor_decision.action == "revise_from_draft":
                    reason = self.state.supervisor_decision.reason or "Supervisor requested a draft revision."
                    if self.state.draft_restarts >= input.options.max_draft_restarts:
                        clarified = await self._wait_for_clarification([reason])
                        if not clarified:
                            if self.state.canceled:
                                return self._result_from_state(
                                    status="cancelled",
                                    extra_warning=self.state.cancel_reason or "Submission canceled.",
                                )
                            return self._result_from_state(
                                status="needs_clarification",
                                extra_warning="Timed out waiting for clarification.",
                            )
                        self.state.restart_point = "acquire"
                    else:
                        self.state.draft_restarts += 1
                        self.state.restart_point = "draft"
                    self.state.open_questions = self._merge_questions(
                        self.state.open_questions,
                        [reason],
                    )
                elif self.state.supervisor_decision.action == "ask_human":
                    clarified = await self._wait_for_clarification(
                        self._merge_questions(
                            self.state.open_questions,
                            self.state.supervisor_decision.questions,
                        )
                    )
                    if not clarified:
                        if self.state.canceled:
                            return self._result_from_state(
                                status="cancelled",
                                extra_warning=self.state.cancel_reason or "Submission canceled.",
                            )
                        return self._result_from_state(
                            status="needs_clarification",
                            extra_warning="Timed out waiting for clarification.",
                        )
                    self.state.restart_point = "acquire"

                await self._advance_iteration(input)

            return self._result_from_state(
                status="needs_clarification",
                extra_warning="Iteration limit reached before acceptance.",
            )
        except Exception as exc:  # noqa: BLE001
            workflow.logger.exception("Spec builder workflow failed: %s", exc)
            return self._result_from_state(status="failed", extra_warning=str(exc))


async def main() -> None:  # pragma: no cover
    """Connect to Temporal and execute a local sample run."""
    client = await Client.connect("localhost:7233", data_converter=pydantic_data_converter)
    sample_input = SpecBuilderInput(
        job_id="spec-builder-sample",
        artifacts=[
            ArtifactInput(
                artifact_id="readme",
                uri=str(Path("README.md").resolve()),
                type="markdown",
                title="Repository README",
            )
        ],
        user_intent="Generate a Temporal implementation-ready spec.",
        output_targets=["markdown_spec", "structured_json"],
        output_directory=".spec_builder_runtime/manual-output",
    )
    result = await client.execute_workflow(
        SpecBuilderWorkflow.run,
        sample_input,
        id="spec-builder-sample",
        task_queue=SPEC_BUILDER_TASK_QUEUE,
    )
    print(result.model_dump_json(indent=2))  # noqa: T201


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
