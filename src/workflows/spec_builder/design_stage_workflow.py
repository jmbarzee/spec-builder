"""Child workflow for the design stage: an agent writes `.twf` and repairs it against the gate."""

from __future__ import annotations

import re
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from pydantic_ai.durable_exec.temporal import PydanticAIWorkflow

    from src.workflows.spec_builder.custom_types import (
        DesignStageInput,
        ValidateTwfInput,
        ValidateTwfOutput,
    )
    from src.workflows.spec_builder.design_agent import DESIGN_AGENT, DESIGN_AGENT_NAME
    from src.workflows.spec_builder.spec_builder_activities import validate_twf_design
    from src.workflows.spec_builder.step_agent import StepRequest, run_step

FENCE = re.compile(r"\A\s*```[\w-]*\n(.*?)\n?```\s*\Z", re.DOTALL)


def _strip_fence(text: str) -> str:
    """Drop one code fence enclosing the whole reply."""
    fenced = FENCE.match(text)
    return fenced.group(1) if fenced else text.strip()


def _repair_prompt(verdict: ValidateTwfOutput) -> str:
    errors = "\n".join(
        f"{d.line}:{d.column} {d.code} {d.message}"
        for d in verdict.diagnostics
        if d.severity == "error"
    )
    return (
        f"The parser reports these errors in your design:\n{errors}\n\n"
        "Fix them and reply with the complete corrected `.twf` design and nothing else."
    )


@workflow.defn
class DesignStageWorkflow(PydanticAIWorkflow):
    """Write a `.twf` design for a markdown spec; stop when it is valid or the budget is spent."""

    __pydantic_ai_agents__ = [DESIGN_AGENT.agent]  # noqa: RUF012

    @workflow.run
    async def run(self, input: DesignStageInput) -> ValidateTwfOutput:
        """Return the verdict on the final attempt."""
        prompt = (
            "Design the Temporal system this specification describes. Reply with the complete "
            f"`.twf` design and nothing else.\n\n{input.markdown_spec}"
        )
        history = []
        for _ in range(1 + input.max_twf_revisions):
            result = await run_step(
                StepRequest(agent=DESIGN_AGENT_NAME, prompt=prompt, message_history=history),
                durable=input.durable_agents,
            )
            if result.output is None:
                raise ApplicationError("The design agent held a tool call.", non_retryable=True)
            history = result.message_history
            verdict = await workflow.execute_activity(
                validate_twf_design,
                ValidateTwfInput(job_id=input.job_id, source=_strip_fence(result.output)),
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            if verdict.valid:
                break
            prompt = _repair_prompt(verdict)
        return verdict
