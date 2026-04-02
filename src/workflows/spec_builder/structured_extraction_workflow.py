"""Child workflow for the structured extraction stage."""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from src.workflows.spec_builder.custom_types import (
        StructuredExtractionInput,
        StructuredExtractionOutput,
    )
    from src.workflows.spec_builder.spec_builder_activities import run_structured_extraction


@workflow.defn
class StructuredExtractionWorkflow:
    """Run stage 2 extraction as a durable child workflow."""

    @workflow.run
    async def run(self, input: StructuredExtractionInput) -> StructuredExtractionOutput:
        """Build the structured intermediate representation."""
        return await workflow.execute_activity(
            run_structured_extraction,
            input,
            start_to_close_timeout=timedelta(minutes=5),
        )
