"""Tests for worker registration."""

from __future__ import annotations

from src.workflows.spec_builder.worker import (
    SPEC_BUILDER_WORKFLOWS,
    build_worker,
)
from src.workflows.spec_builder.spec_builder_activities import SPEC_BUILDER_ACTIVITIES


class TestSpecBuilderWorker:
    """Behavior tests for worker registration."""

    def test_worker_module_should_expose_all_workflows_and_activities_for_future_gui_use(
        self,
    ) -> None:
        """
        Scenario: Worker registration surface
        Given the future GUI will start or share a worker process
        When the worker module is imported
        Then it should expose the full workflow and activity registration surface
        """
        workflow_names = {workflow_class.__name__ for workflow_class in SPEC_BUILDER_WORKFLOWS}
        activity_names = {activity.__name__ for activity in SPEC_BUILDER_ACTIVITIES}

        assert "SpecBuilderWorkflow" in workflow_names
        assert "AcquireContentWorkflow" in workflow_names
        assert "persist_final_outputs" in activity_names
        assert callable(build_worker)
