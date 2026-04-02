"""Child workflow for the supervisor decision stage."""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from src.workflows.spec_builder.custom_types import SupervisorDecision, SupervisorDecisionInput
    from src.workflows.spec_builder.spec_builder_activities import run_supervisor_decision


@workflow.defn
class SupervisorDecisionWorkflow:
    """Run stage 7 supervisor decision as a durable child workflow."""

    @workflow.run
    async def run(self, input: SupervisorDecisionInput) -> SupervisorDecision:
        """Choose the next bounded workflow action."""
        return await workflow.execute_activity(
            run_supervisor_decision,
            input,
            start_to_close_timeout=timedelta(minutes=5),
        )
