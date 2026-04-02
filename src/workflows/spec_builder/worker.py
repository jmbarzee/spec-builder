"""Worker bootstrap for the spec builder workflow package."""

from __future__ import annotations

import asyncio

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from src.workflows.spec_builder.acquire_content_workflow import AcquireContentWorkflow
from src.workflows.spec_builder.custom_types import SPEC_BUILDER_TASK_QUEUE
from src.workflows.spec_builder.draft_spec_workflow import DraftSpecWorkflow
from src.workflows.spec_builder.extraction_critic_workflow import ExtractionCriticWorkflow
from src.workflows.spec_builder.intent_classification_workflow import (
    IntentClassificationWorkflow,
)
from src.workflows.spec_builder.spec_builder_activities import SPEC_BUILDER_ACTIVITIES
from src.workflows.spec_builder.spec_builder_workflow import SpecBuilderWorkflow
from src.workflows.spec_builder.structured_extraction_workflow import (
    StructuredExtractionWorkflow,
)
from src.workflows.spec_builder.supervisor_decision_workflow import (
    SupervisorDecisionWorkflow,
)
from src.workflows.spec_builder.temporal_best_practices_review_workflow import (
    TemporalBestPracticesReviewWorkflow,
)

SPEC_BUILDER_WORKFLOWS = [
    SpecBuilderWorkflow,
    AcquireContentWorkflow,
    StructuredExtractionWorkflow,
    IntentClassificationWorkflow,
    ExtractionCriticWorkflow,
    DraftSpecWorkflow,
    TemporalBestPracticesReviewWorkflow,
    SupervisorDecisionWorkflow,
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
    client = await Client.connect("localhost:7233", data_converter=pydantic_data_converter)
    worker = build_worker(client)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
