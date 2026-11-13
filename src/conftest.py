"""Test configuration for workflow tests."""

from collections.abc import AsyncGenerator

import pytest_asyncio
from pydantic_ai.durable_exec.temporal import PydanticAIPlugin
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker._worker import load_default_build_id


def pytest_collection_finish() -> None:
    """Hash the loaded modules once, outside any test's timeout.

    The SDK memoizes the result, so the first ``Worker`` in a test would
    otherwise pay for it and can exceed the per-test limit. A session fixture
    would not help: pytest-timeout also bounds fixture setup.
    """
    load_default_build_id()


@pytest_asyncio.fixture(scope="session")
async def env() -> AsyncGenerator[WorkflowEnvironment, None]:
    """Create a Temporal test workflow environment."""
    env = await WorkflowEnvironment.start_time_skipping(
        plugins=[PydanticAIPlugin()],
    )
    yield env
    await env.shutdown()


@pytest_asyncio.fixture
async def client(env: WorkflowEnvironment) -> Client:
    """Create a Temporal test client."""
    return env.client
