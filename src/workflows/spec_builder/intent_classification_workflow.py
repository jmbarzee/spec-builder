"""Child workflow for the intent classification stage."""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from src.workflows.spec_builder.custom_types import (
        IntentClassificationInput,
        IntentClassificationOutput,
    )
    from src.workflows.spec_builder.spec_builder_activities import run_intent_classification


@workflow.defn
class IntentClassificationWorkflow:
    """Run stage 3 intent classification as a durable child workflow."""

    @workflow.run
    async def run(self, input: IntentClassificationInput) -> IntentClassificationOutput:
        """Determine the target spec framing for the submission."""
        return await workflow.execute_activity(
            run_intent_classification,
            input,
            start_to_close_timeout=timedelta(minutes=5),
        )
