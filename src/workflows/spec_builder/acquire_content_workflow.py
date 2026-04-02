"""Child workflow for the acquisition stage."""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from src.workflows.spec_builder.custom_types import AcquireContentInput, AcquisitionOutput
    from src.workflows.spec_builder.spec_builder_activities import acquire_content


@workflow.defn
class AcquireContentWorkflow:
    """Run stage 1 acquisition as a durable child workflow."""

    @workflow.run
    async def run(self, input: AcquireContentInput) -> AcquisitionOutput:
        """Normalize source artifacts into an acquisition bundle."""
        return await workflow.execute_activity(
            acquire_content,
            input,
            start_to_close_timeout=timedelta(minutes=5),
        )
