"""Child workflow for the Temporal best-practices review stage."""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from src.workflows.spec_builder.custom_types import (
        BestPracticesReviewOutput,
        RunBestPracticesReviewActivityInput,
        TemporalBestPracticesReviewInput,
    )
    from src.workflows.spec_builder.spec_builder_activities import (
        retrieve_best_practices_passages,
        run_best_practices_review,
    )


@workflow.defn
class TemporalBestPracticesReviewWorkflow:
    """Run stage 6 best-practices review as a durable child workflow."""

    @workflow.run
    async def run(self, input: TemporalBestPracticesReviewInput) -> BestPracticesReviewOutput:
        """Retrieve best-practices passages and review the draft against them."""
        passages = await workflow.execute_activity(
            retrieve_best_practices_passages,
            input,
            start_to_close_timeout=timedelta(minutes=5),
        )
        return await workflow.execute_activity(
            run_best_practices_review,
            RunBestPracticesReviewActivityInput(
                job_id=input.job_id,
                draft=input.draft,
                passages=passages,
            ),
            start_to_close_timeout=timedelta(minutes=5),
        )
