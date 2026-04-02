"""Child workflow for the extraction critic stage."""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from src.workflows.spec_builder.custom_types import CriticOutput, ExtractionCriticInput
    from src.workflows.spec_builder.spec_builder_activities import run_extraction_critic


@workflow.defn
class ExtractionCriticWorkflow:
    """Run stage 4 extraction critique as a durable child workflow."""

    @workflow.run
    async def run(self, input: ExtractionCriticInput) -> CriticOutput:
        """Evaluate whether the extraction is ready for drafting."""
        return await workflow.execute_activity(
            run_extraction_critic,
            input,
            start_to_close_timeout=timedelta(minutes=5),
        )
