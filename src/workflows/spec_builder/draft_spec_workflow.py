"""Child workflow for the draft generation stage."""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from src.workflows.spec_builder.custom_types import DraftSpecInput, DraftSpecOutput
    from src.workflows.spec_builder.spec_builder_activities import run_draft_spec


@workflow.defn
class DraftSpecWorkflow:
    """Run stage 5 draft generation as a durable child workflow."""

    @workflow.run
    async def run(self, input: DraftSpecInput) -> DraftSpecOutput:
        """Generate the markdown spec and structured JSON companion."""
        return await workflow.execute_activity(
            run_draft_spec,
            input,
            start_to_close_timeout=timedelta(minutes=5),
        )
