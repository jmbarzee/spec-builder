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

    from src.workflows.spec_builder.custom_types import (
        AcquireContentInput,
        ApproveDraftInput,
        CancelJobInput,
        ClarificationRecord,
        DraftMetadata,
        DecideToolCallsInput,
        DraftSpecInput,
        ExtractionCriticInput,
        ForceFinalizeDraftInput,
        IntentClassificationInput,
        PersistFinalOutputsInput,
        RejectDraftInput,
        RestartPoint,
        SPEC_BUILDER_TASK_QUEUE,
        SpecBuilderInput,
        SpecBuilderLiveState,
        SpecBuilderResult,
        SpecBuilderState,
        SpecBuilderStatus,
        StructuredExtractionInput,
        SubmitClarificationInput,
        SupervisorDecisionInput,
        TemporalBestPracticesReviewInput,
        ToolHold,
        TraceabilitySummary,
    )
    from src.workflows.spec_builder.spec_builder_activities import (
        acquire_content,
        persist_final_outputs,
        run_draft_spec,
        run_extraction_critic,
        run_intent_classification,
        run_structured_extraction,
        run_supervisor_decision,
    )
    from src.workflows.spec_builder.step_agent import StepRequest, run_step
    from src.workflows.spec_builder.temporal_best_practices_review_workflow import (
        TemporalBestPracticesReviewWorkflow,
    )

@workflow.defn
class SpecBuilderWorkflow:
    """Durable fixed-pipeline spec builder workflow."""

    def __init__(self) -> None:
        """Initialize durable and in-memory control state for one workflow execution."""
        
        # state is the durable workflow snapshot that survives replay and continue-as-new.
        self.state: SpecBuilderState | None = None
        
        # These fields cache signal-side control flow derived from durable state so the workflow can
        # make decisions without repeatedly scanning prior requests or mutating state mid-query.
        self._processed_request_ids: set[str] = set()
        self._approval_granted = False
        self._approval_note: str | None = None
        self._rejection_payload: RejectDraftInput | None = None

    
    def _ensure_state(self) -> SpecBuilderState:
        #TODO: Validate if this helper method is really necessary
        if self.state is None:
            raise RuntimeError("Workflow state has not been initialized yet.")
        return self.state

    def _merge_questions(self, *question_groups: list[str]) -> list[str]:
        # Keep the first-seen ordering stable so the UI and downstream prompts do not thrash when
        # the same question is surfaced by multiple stages.
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

    def _record_revision_notes(self, *note_groups: list[str]) -> None:
        state = self._ensure_state()
        state.revision_notes = self._merge_questions(state.revision_notes, *note_groups)

    def _mark_clarifications_applied(self) -> None:
        state = self._ensure_state()
        state.last_applied_clarification_count = len(state.clarifications)

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
        # The draft already carries explicit traceability entries. The summary adds a compact
        # "what is still uncovered?" view for the GUI without exposing the entire workflow state.
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
            pending_tool_calls=state.tool_hold.calls if state.tool_hold else [],
            open_questions=state.open_questions,
            warnings=state.warnings,
            artifact_uris=state.artifact_uris,
            current_draft_metadata=self._current_draft_metadata(),
            current_supervisor_action=(
                state.supervisor_decision.action if state.supervisor_decision else None
            ),
            final_status=state.final_result.status if state.final_result else None,
        )

    def _live_state(self) -> SpecBuilderLiveState:
        state = self._ensure_state()
        return SpecBuilderLiveState(
            status=self._status(),
            current_draft=state.draft,
            best_practices_violations=(
                state.best_practices_review.violations if state.best_practices_review else []
            ),
            traceability_summary=self._traceability_summary(),
        )

    def _has_force_finalize_request(self) -> bool:
        state = self._ensure_state()
        return (
            state.force_finalize_requested
            and state.draft is not None
            and state.best_practices_review is not None
        )

    def _should_auto_accept_supervisor_decision(self) -> bool:
        state = self._ensure_state()
        # The supervisor may still ask for a revision even when the critic says the extraction is
        # sound and best-practices review only found non-blocking warnings. In that narrow case we
        # collapse the decision to `accept` to avoid churn from low-signal rewrite loops.
        if (
            state.supervisor_decision is None
            or state.extraction_critic is None
            or state.best_practices_review is None
        ):
            return False
        if state.supervisor_decision.action not in {"revise_from_draft", "revise_from_extraction"}:
            return False
        if state.extraction_critic.recommended_action != "proceed":
            return False
        if state.extraction_critic.blocking_issues:
            return False
        if state.best_practices_review.verdict != "pass_with_warnings":
            return False
        return not any(
            violation.severity == "high" for violation in state.best_practices_review.violations
        )

    def _prepare_restart(self, restart_point: RestartPoint) -> None:
        state = self._ensure_state()
        # Restarting from a checkpoint means two things:
        # 1. Preserve the most recent outputs as "previous_*" context for the next attempt.
        # 2. Clear every downstream artifact that is no longer valid from that checkpoint onward.
        state.pending_human_action = None
        self._approval_granted = False
        self._approval_note = None
        if restart_point in {"acquire", "extract"} and state.extraction is not None:
            state.previous_extraction = state.extraction
        if state.draft is not None:
            state.previous_draft = state.draft
        if state.best_practices_review is not None:
            state.previous_best_practices_review = state.best_practices_review

        if restart_point == "acquire":
            state.acquisition = None
            state.artifact_uris.pop("source_bundle", None)
        if restart_point in {"acquire", "extract"}:
            state.extraction = None
            state.intent = None
            state.extraction_critic = None
            for key in (
                "structured_extraction",
                "intent_classification",
                "extraction_critic",
            ):
                state.artifact_uris.pop(key, None)
        state.draft = None
        state.best_practices_review = None
        state.supervisor_decision = None
        for key in (
            "draft_markdown",
            "draft_structured",
            "best_practices_review",
            "supervisor_decision",
        ):
            state.artifact_uris.pop(key, None)

    async def _force_finalize(self, input: SpecBuilderInput) -> SpecBuilderResult:
        state = self._ensure_state()
        # Force-finalize is intentionally opinionated.
        # We only allow it once a reviewed draft exists, and we annotate the output so downstream
        # readers can see that policy concerns were waived.
        force_finalize_warnings = [
            "Force-finalized from the UI despite high-risk policy violations."
        ]
        if state.force_finalize_reason:
            force_finalize_warnings.append(
                f"Force-finalize note: {state.force_finalize_reason}"
            )
            state.approval_note = state.force_finalize_reason
        state.warnings = self._merge_questions(state.warnings, force_finalize_warnings)
        state.pending_human_action = None
        state.force_finalize_requested = False
        state.force_finalize_reason = None
        return await self._complete(input)

    async def _execute_stage_activity(
        self,
        activity_fn: Any,
        stage_input: object,
        stage_name: str,
    ) -> Any:
        workflow.logger.info(
            "Starting activity stage %s for job %s",
            stage_name,
            self._ensure_state().job_id,
        )
        return await workflow.execute_activity(
            activity_fn,
            stage_input,
            start_to_close_timeout=timedelta(minutes=5),
        )

    async def _execute_best_practices_review(
        self,
        stage_input: TemporalBestPracticesReviewInput,
    ) -> Any:
        workflow.logger.info(
            "Starting child stage %s for job %s",
            "best_practices_review",
            self._ensure_state().job_id,
        )
        return await workflow.execute_child_workflow(
            TemporalBestPracticesReviewWorkflow.run,
            stage_input,
            # Use a unique child workflow ID per attempt so a restarted draft review never collides
            # with a previously-completed child from the same parent execution.
            id=f"{workflow.info().workflow_id}-best-practices-review-{workflow.uuid4()}",
            task_queue=SPEC_BUILDER_TASK_QUEUE,
            parent_close_policy=ParentClosePolicy.TERMINATE,
        )

    # Queries expose progressively richer read models. The GUI mostly uses `get_live_state()` to
    # fetch all active-job data in one RPC, while narrower queries remain useful for debugging.
    #TODO: Validate which of these are actually necessary and remove the rest if they are not being used by the GUI or tests.
    @workflow.query
    def get_status(self) -> SpecBuilderStatus:
        """Return the current workflow status snapshot."""
        return self._status()

    @workflow.query
    def get_live_state(self) -> SpecBuilderLiveState:
        """Return the current workflow state needed by the GUI in one snapshot."""
        return self._live_state()

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
        # Every external signal carries a request ID so retries from the GUI remain idempotent.
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
    def force_finalize_draft(self, payload: ForceFinalizeDraftInput) -> None:
        """Force-finalize the current reviewed draft."""
        if self.state is None:
            workflow.logger.warning("Force finalize received before state initialization.")
            return
        if self._seen_request(payload.request_id):
            return
        self._record_request(payload.request_id)
        if self.state.draft is None or self.state.best_practices_review is None:
            self.state.warnings = self._merge_questions(
                self.state.warnings,
                ["Received force_finalize_draft before a reviewed draft was available."],
            )
            return
        self.state.force_finalize_requested = True
        self.state.force_finalize_reason = payload.reason

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
    def decide_tool_calls(self, payload: DecideToolCallsInput) -> None:
        """Decide every pending tool call of the current hold at once."""
        if self.state is None:
            workflow.logger.warning("Tool decisions received before state initialization.")
            return
        if self._seen_request(payload.request_id):
            return
        self._record_request(payload.request_id)
        hold = self.state.tool_hold
        # Pydantic AI resumes only a whole batch, so a partial or repeated decision is refused.
        if (
            hold is None
            or hold.decisions
            or set(payload.decisions) != {call.tool_call_id for call in hold.calls}
        ):
            self.state.warnings = self._merge_questions(
                self.state.warnings,
                ["Received decide_tool_calls that does not match the undecided pending calls."],
            )
            return
        hold.decisions = payload.decisions

    @workflow.signal
    def cancel_job(self, payload: CancelJobInput) -> None:
        """Cancel the current submission."""
        #TODO: Validate if this actually works, it's been buggy
        if self.state is None:
            workflow.logger.warning("Cancellation received before state initialization.")
            return
        if self._seen_request(payload.request_id):
            return
        self._record_request(payload.request_id)
        self.state.canceled = True
        self.state.cancel_reason = payload.reason

    async def _wait_for_clarification(self, questions: list[str]) -> str:
        state = self._ensure_state()
        state.phase = "waiting_for_human"
        state.pending_human_action = "clarification"
        state.open_questions = self._merge_questions(state.open_questions, questions)
        # Only new clarifications should wake this wait. Existing clarifications were already folded
        # into a prior attempt, so we capture a durable baseline count before blocking.
        baseline = state.last_applied_clarification_count
        if len(state.clarifications) <= baseline and not state.canceled:
            try:
                await workflow.wait_condition(
                    lambda: self._ensure_state().canceled
                    or self._ensure_state().force_finalize_requested
                    or len(self._ensure_state().clarifications) > baseline,
                    timeout=HUMAN_RESPONSE_TIMEOUT,
                    timeout_summary="waiting for clarification",
                )
            except TimeoutError:
                return "timeout"
        if state.force_finalize_requested:
            return "force_finalize"
        if state.canceled:
            return "canceled"
        state.pending_human_action = None
        return "clarified"

    async def _wait_for_approval(self) -> str:
        state = self._ensure_state()
        state.phase = "waiting_for_approval"
        state.pending_human_action = "approval"
        # Approval, rejection, cancellation, and force-finalize all share the same wait because the
        # caller needs to branch differently depending on which human action arrived first.
        if not self._approval_granted and self._rejection_payload is None and not state.canceled:
            try:
                await workflow.wait_condition(
                    lambda: self._approval_granted
                    or self._rejection_payload is not None
                    or self._ensure_state().force_finalize_requested
                    or self._ensure_state().canceled,
                    timeout=HUMAN_RESPONSE_TIMEOUT,
                    timeout_summary="waiting for approval",
                )
            except TimeoutError:
                return "timeout"
        if state.force_finalize_requested:
            return "force_finalize"
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

    async def _run_agent_step(
        self,
        input: SpecBuilderInput,
        agent: str,
        prompt: str,
    ) -> str | None:
        """Run a step agent to its output, holding for a human decision at each held tool call.

        Returns None when the hold timed out or the job was canceled (`state.canceled` tells which).
        A stage that continued as new re-enters here and resumes the hold it carried.
        """
        state = self._ensure_state()
        durable = input.options.durable_agents
        result = None
        if state.tool_hold is None:
            result = await run_step(StepRequest(agent=agent, prompt=prompt), durable=durable)
        while True:
            if result is not None:
                if not result.held:
                    return result.output
                state.tool_hold = ToolHold(
                    agent=agent,
                    phase=state.phase,
                    message_history=result.message_history,
                    calls=result.held,
                    deadline=workflow.now() + HUMAN_RESPONSE_TIMEOUT,
                )
            hold = state.tool_hold
            if hold is None or hold.agent != agent:
                raise RuntimeError(f"Carried hold does not belong to agent {agent!r}.")
            state.phase = "waiting_for_approval"
            state.pending_human_action = "tool_approval"
            # The deadline is absolute, so a hold that rolls over keeps its original 24 hours.
            try:
                await workflow.wait_condition(
                    lambda hold=hold: bool(hold.decisions)
                    or state.canceled
                    or workflow.info().is_continue_as_new_suggested(),
                    timeout=max(hold.deadline - workflow.now(), timedelta(0)),
                    timeout_summary="waiting for tool-call approval",
                )
            except TimeoutError:
                self._release_hold()
                return None
            if state.canceled:
                self._release_hold()
                return None
            if workflow.info().is_continue_as_new_suggested():
                await self._continue_as_new(input)
            self._release_hold()
            result = await run_step(
                StepRequest(
                    agent=agent,
                    message_history=hold.message_history,
                    approvals=hold.decisions,
                ),
                durable=durable,
            )

    def _release_hold(self) -> None:
        state = self._ensure_state()
        if state.tool_hold is not None:
            state.phase = state.tool_hold.phase
        state.tool_hold = None
        state.pending_human_action = None

    async def _continue_as_new(self, input: SpecBuilderInput) -> None:
        # Continue-as-new trims workflow history, but only after outstanding signal/query handlers
        # have drained so we do not drop an in-flight human decision during rollover.
        state = self._ensure_state()
        await workflow.wait_condition(workflow.all_handlers_finished)
        resumed_state = state.model_copy(
            deep=True,
            update={
                "iterations_in_current_run": 0,
                "continuation_count": state.continuation_count + 1,
            },
        )
        workflow.continue_as_new(
            input.model_copy(deep=True, update={"resume_state": resumed_state})
        )

    async def _advance_iteration(self, input: SpecBuilderInput) -> None:
        state = self._ensure_state()
        state.iteration += 1
        state.iterations_in_current_run += 1
        if workflow.info().is_continue_as_new_suggested():
            await self._continue_as_new(input)

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

    def _load_state(self, input: SpecBuilderInput) -> None:
        self.state = (
            input.resume_state.model_copy(deep=True)
            if input.resume_state
            else SpecBuilderState(job_id=input.job_id)
        )
        self._processed_request_ids = set(self.state.processed_request_ids)
        if self.state.job_id != input.job_id:
            self.state.job_id = input.job_id

    @workflow.run
    async def run(self, input: SpecBuilderInput) -> SpecBuilderResult:
        """Run the fixed spec-builder pipeline with bounded iteration."""
        # A continued execution resumes from the last durable checkpoint; a fresh execution starts
        # from an empty state rooted at the provided job ID.
        self._load_state(input)

        try:
            while self.state.iteration < input.options.max_iterations:
                # Force-finalize and cancellation are checked at the top of each attempt so the
                # workflow can exit cleanly before starting another expensive activity chain.
                if self._has_force_finalize_request():
                    return await self._force_finalize(input)

                if self.state.canceled:
                    return self._result_from_state(
                        status="cancelled",
                        extra_warning=self.state.cancel_reason or "Submission canceled.",
                    )

                if self.state.restart_point == "acquire":
                    # Acquisition only reruns when the user asks to revisit source artifacts from
                    # scratch. Restarts from later checkpoints preserve the last acquisition output.
                    self.state.phase = "acquire"
                    self.state.acquisition = await self._execute_stage_activity(
                        acquire_content,
                        AcquireContentInput(
                            job_id=input.job_id,
                            artifacts=input.artifacts,
                            clarifications=self.state.clarifications,
                        ),
                        "acquire",
                    )
                    if self.state.acquisition.source_bundle_uri:
                        self.state.artifact_uris["source_bundle"] = self.state.acquisition.source_bundle_uri
                    self.state.warnings = self._merge_questions(
                        self.state.warnings,
                        self.state.acquisition.warnings,
                    )

                if self.state.restart_point in {"acquire", "extract"}:
                    # Extraction and classification are paired because a new extraction invalidates
                    # any prior interpretation of intent.
                    self.state.phase = "extract"
                    self.state.extraction = await self._execute_stage_activity(
                        run_structured_extraction,
                        StructuredExtractionInput(
                            job_id=input.job_id,
                            acquisition=self.state.acquisition,
                            clarifications=self.state.clarifications,
                            revision_notes=self.state.revision_notes,
                            previous_extraction=(
                                self.state.previous_extraction
                                if self.state.restart_point == "extract"
                                else None
                            ),
                        ),
                        "extract",
                    )
                    self._mark_clarifications_applied()
                    self.state.previous_extraction = None
                    if self.state.extraction.artifact_uri:
                        self.state.artifact_uris["structured_extraction"] = self.state.extraction.artifact_uri

                    self.state.phase = "classify"
                    self.state.intent = await self._execute_stage_activity(
                        run_intent_classification,
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
                self.state.extraction_critic = await self._execute_stage_activity(
                    run_extraction_critic,
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
                    # Critic-driven re-extraction is the automated "tighten the inputs and try
                    # again" path. Once we exhaust the restart budget, we escalate to the user.
                    self._record_revision_notes(
                        self.state.extraction_critic.blocking_issues,
                        self.state.extraction_critic.missing_information,
                    )
                    if self.state.extraction_restarts >= input.options.max_extraction_restarts:
                        clarification_outcome = await self._wait_for_clarification(
                            self.state.open_questions
                        )
                        if clarification_outcome == "force_finalize":
                            return await self._force_finalize(input)
                        if clarification_outcome == "canceled":
                            return self._result_from_state(
                                status="cancelled",
                                extra_warning=self.state.cancel_reason or "Submission canceled.",
                            )
                        if clarification_outcome == "timeout":
                            return self._result_from_state(
                                status="needs_clarification",
                                extra_warning="Timed out waiting for clarification.",
                            )
                    else:
                        self.state.extraction_restarts += 1
                    self.state.restart_point = "extract"
                    self._prepare_restart("extract")
                    await self._advance_iteration(input)
                    continue

                if self.state.extraction_critic.recommended_action == "ask_human":
                    # The critic can pause the workflow before drafting when the extracted model is
                    # plausible but under-specified enough that only a human can close the gap.
                    clarification_outcome = await self._wait_for_clarification(
                        self.state.open_questions
                    )
                    if clarification_outcome == "force_finalize":
                        return await self._force_finalize(input)
                    if clarification_outcome == "canceled":
                        return self._result_from_state(
                            status="cancelled",
                            extra_warning=self.state.cancel_reason or "Submission canceled.",
                        )
                    if clarification_outcome == "timeout":
                        return self._result_from_state(
                            status="needs_clarification",
                            extra_warning="Timed out waiting for clarification.",
                        )
                    self.state.restart_point = "extract"
                    self._prepare_restart("extract")
                    await self._advance_iteration(input)
                    continue

                self.state.phase = "draft"
                self.state.draft = await self._execute_stage_activity(
                    run_draft_spec,
                    DraftSpecInput(
                        job_id=input.job_id,
                        extraction=self.state.extraction,
                        intent=self.state.intent,
                        clarifications=self.state.clarifications,
                        revision_notes=self.state.revision_notes,
                        previous_draft=self.state.previous_draft,
                        previous_best_practices_review=self.state.previous_best_practices_review,
                    ),
                    "draft",
                )
                self._mark_clarifications_applied()
                self.state.revision_notes = []
                self.state.previous_draft = None
                self.state.previous_best_practices_review = None
                if self.state.draft.markdown_spec_uri:
                    self.state.artifact_uris["draft_markdown"] = self.state.draft.markdown_spec_uri
                if self.state.draft.structured_spec_uri:
                    self.state.artifact_uris["draft_structured"] = self.state.draft.structured_spec_uri
                self.state.open_questions = self._merge_questions(
                    self.state.open_questions,
                    self.state.draft.open_questions,
                )

                self.state.phase = "best_practices_review"
                # Best-practices review runs as a child workflow because it is substantial enough to
                # deserve its own isolated execution history and retry semantics.
                self.state.best_practices_review = await self._execute_best_practices_review(
                    TemporalBestPracticesReviewInput(
                        job_id=input.job_id,
                        draft=self.state.draft,
                        corpus_uri=input.options.best_practices_corpus_uri,
                    ),
                )
                if self.state.best_practices_review.artifact_uri:
                    self.state.artifact_uris["best_practices_review"] = (
                        self.state.best_practices_review.artifact_uri
                    )

                if self._has_force_finalize_request():
                    return await self._force_finalize(input)

                self.state.phase = "supervisor"
                self.state.supervisor_decision = await self._execute_stage_activity(
                    run_supervisor_decision,
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

                if self._has_force_finalize_request():
                    return await self._force_finalize(input)

                if self._should_auto_accept_supervisor_decision():
                    self.state.supervisor_decision = self.state.supervisor_decision.model_copy(
                        update={
                            "action": "accept",
                            "reason": (
                                self.state.supervisor_decision.reason
                                or "No high-severity best-practices issues remain after review."
                            ),
                            "questions": [],
                        }
                    )

                if self.state.supervisor_decision.action == "accept":
                    # "Accept" can still be subject to a final human gate. Rejection at this point
                    # translates into a targeted restart rather than a terminal failure.
                    if input.options.require_human_approval:
                        approval_outcome = await self._wait_for_approval()
                        if approval_outcome == "approved":
                            return await self._complete(input)
                        if approval_outcome == "force_finalize":
                            return await self._force_finalize(input)
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
                            self._record_revision_notes([payload.reason])
                            if payload.restart_point == "acquire":
                                self.state.extraction_restarts += 1
                                self.state.restart_point = "acquire"
                                self._prepare_restart("acquire")
                            elif payload.restart_point == "extract":
                                self.state.extraction_restarts += 1
                                self.state.restart_point = "extract"
                                self._prepare_restart("extract")
                            else:
                                self.state.draft_restarts += 1
                                self.state.restart_point = "draft"
                                self._prepare_restart("draft")
                            await self._advance_iteration(input)
                            continue
                    else:
                        return await self._complete(input)

                if self.state.supervisor_decision.action == "revise_from_extraction":
                    # A supervisor-directed extraction revision rewinds farther upstream than a
                    # draft-only revision because the issue is with the interpreted source model.
                    reason = self.state.supervisor_decision.reason or "Supervisor requested re-extraction."
                    self._record_revision_notes([reason])
                    if self.state.extraction_restarts >= input.options.max_extraction_restarts:
                        clarification_outcome = await self._wait_for_clarification([reason])
                        if clarification_outcome == "force_finalize":
                            return await self._force_finalize(input)
                        if clarification_outcome == "canceled":
                            return self._result_from_state(
                                status="cancelled",
                                extra_warning=self.state.cancel_reason or "Submission canceled.",
                            )
                        if clarification_outcome == "timeout":
                            return self._result_from_state(
                                status="needs_clarification",
                                extra_warning="Timed out waiting for clarification.",
                            )
                    else:
                        self.state.extraction_restarts += 1
                    self.state.restart_point = "extract"
                    self._prepare_restart("extract")
                    self.state.open_questions = self._merge_questions(
                        self.state.open_questions,
                        [reason],
                    )
                elif self.state.supervisor_decision.action == "revise_from_draft":
                    # Draft revisions preserve extraction artifacts and feed the previous draft back
                    # into the next drafting attempt as revision context.
                    reason = self.state.supervisor_decision.reason or "Supervisor requested a draft revision."
                    self._record_revision_notes([reason])
                    if self.state.draft_restarts >= input.options.max_draft_restarts:
                        clarification_outcome = await self._wait_for_clarification([reason])
                        if clarification_outcome == "force_finalize":
                            return await self._force_finalize(input)
                        if clarification_outcome == "canceled":
                            return self._result_from_state(
                                status="cancelled",
                                extra_warning=self.state.cancel_reason or "Submission canceled.",
                            )
                        if clarification_outcome == "timeout":
                            return self._result_from_state(
                                status="needs_clarification",
                                extra_warning="Timed out waiting for clarification.",
                            )
                        self.state.restart_point = "draft"
                        self._prepare_restart("draft")
                    else:
                        self.state.draft_restarts += 1
                        self.state.restart_point = "draft"
                        self._prepare_restart("draft")
                    self.state.open_questions = self._merge_questions(
                        self.state.open_questions,
                        [reason],
                    )
                elif self.state.supervisor_decision.action == "ask_human":
                    # The supervisor has the last word on whether the workflow can proceed without
                    # more human input, even if earlier stages were satisfied.
                    clarification_outcome = await self._wait_for_clarification(
                        self._merge_questions(
                            self.state.open_questions,
                            self.state.supervisor_decision.questions,
                        )
                    )
                    if clarification_outcome == "force_finalize":
                        return await self._force_finalize(input)
                    if clarification_outcome == "canceled":
                        return self._result_from_state(
                            status="cancelled",
                            extra_warning=self.state.cancel_reason or "Submission canceled.",
                        )
                    if clarification_outcome == "timeout":
                        return self._result_from_state(
                            status="needs_clarification",
                            extra_warning="Timed out waiting for clarification.",
                        )
                    self.state.restart_point = "draft"
                    self._prepare_restart("draft")

                # Any non-terminal supervisor branch loops back through the pipeline from the
                # selected restart point, possibly continuing as new if history has grown too large.
                await self._advance_iteration(input)

            return self._result_from_state(
                status="needs_clarification",
                extra_warning="Iteration limit reached before acceptance.",
            )
        except Exception as exc:  #
            workflow.logger.exception("Spec builder workflow failed: %s", exc)
            return self._result_from_state(status="failed", extra_warning=str(exc))
