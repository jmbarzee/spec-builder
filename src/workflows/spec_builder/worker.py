"""Worker bootstrap for the spec builder workflow package."""

from __future__ import annotations

import asyncio

from pydantic_ai.durable_exec.temporal import PydanticAIPlugin
from temporalio.client import Client
from temporalio.worker import Worker

from src.workflows.spec_builder.architecture import architecture_mode_opted_in, check_skills
from src.workflows.spec_builder.custom_types import SPEC_BUILDER_TASK_QUEUE
from src.workflows.spec_builder.design_stage_workflow import DesignStageWorkflow
from src.workflows.spec_builder.model_executor import validate_settings
from src.workflows.spec_builder.spec_builder_activities import SPEC_BUILDER_ACTIVITIES
from src.workflows.spec_builder.spec_builder_workflow import SpecBuilderWorkflow
from src.workflows.spec_builder.temporal_best_practices_review_workflow import (
    TemporalBestPracticesReviewWorkflow,
)

SPEC_BUILDER_WORKFLOWS = [
    SpecBuilderWorkflow,
    TemporalBestPracticesReviewWorkflow,
    DesignStageWorkflow,
]


def build_worker(client: Client) -> Worker:
    """Build a worker with all workflows and activities registered."""
    return Worker(
        client,
        task_queue=SPEC_BUILDER_TASK_QUEUE,
        workflows=SPEC_BUILDER_WORKFLOWS,
        activities=SPEC_BUILDER_ACTIVITIES,
    )


async def main() -> None:
    """Connect to the local Temporal dev server and run the worker."""
    validate_settings()
    if architecture_mode_opted_in():
        check_skills()
    client = await Client.connect("localhost:7233", plugins=[PydanticAIPlugin()])
    worker = build_worker(client)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
