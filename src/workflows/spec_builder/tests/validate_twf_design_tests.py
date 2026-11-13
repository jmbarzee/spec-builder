"""Tests for the `.twf` validation gate activity. These run the real `twf` CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from src.workflows.spec_builder import spec_builder_activities
from src.workflows.spec_builder.custom_types import ValidateTwfInput
from src.workflows.spec_builder.spec_builder_activities import (
    _path_from_uri,
    validate_twf_design,
)

VALID_DESIGN = 'workflow Foo():\n\tactivity Bar()\n\nactivity Bar() -> (string):\n\treturn "x"\n'
UNDEFINED_ACTIVITY_DESIGN = "workflow Foo():\n\tactivity Bar()\n"


@pytest.fixture(autouse=True)
def runtime_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep each test's runtime directory out of the working tree."""
    monkeypatch.setattr(spec_builder_activities, "RUNTIME_ROOT", tmp_path)


class TestValidateTwfDesign:
    """Behavior tests for the validation gate."""

    @pytest.mark.asyncio
    async def test_valid_design_should_pass_and_record_parse_and_graph_output(self) -> None:
        """Scenario: Valid design
        Given a well-formed design
        When the gate runs
        Then it is valid, and parse and graph output sit in the runtime directory and artifact URIs
        """
        result = await ActivityEnvironment().run(
            validate_twf_design, ValidateTwfInput(job_id="job-twf", source=VALID_DESIGN)
        )

        assert result.valid
        assert result.diagnostics == []
        assert set(result.artifact_uris) == {"twf_source", "twf_parse_json", "twf_graph_json"}
        parse = json.loads(_path_from_uri(result.artifact_uris["twf_parse_json"]).read_text())
        graph = json.loads(_path_from_uri(result.artifact_uris["twf_graph_json"]).read_text())
        assert parse["definitions"][0]["name"] == "Foo"
        assert {node["name"] for node in graph["graph"]["nodes"]} == {"Foo", "Bar"}

    @pytest.mark.asyncio
    async def test_invalid_design_should_return_positioned_diagnostics(self) -> None:
        """Scenario: Design with a resolve error
        Given a design that calls an undefined activity
        When the gate runs
        Then it is invalid and the diagnostic carries severity, code, line and column
        """
        result = await ActivityEnvironment().run(
            validate_twf_design,
            ValidateTwfInput(job_id="job-twf", source=UNDEFINED_ACTIVITY_DESIGN),
        )

        assert not result.valid
        [diagnostic] = result.diagnostics
        assert (diagnostic.severity, diagnostic.code) == ("error", "UNDEFINED_ACTIVITY")
        assert (diagnostic.line, diagnostic.column) == (2, 11)

    @pytest.mark.asyncio
    async def test_space_indented_design_should_be_reformatted_before_the_check(self) -> None:
        """Scenario: Space indentation
        Given a valid design indented with spaces
        When the gate runs
        Then it is rewritten with tabs and passes
        """
        result = await ActivityEnvironment().run(
            validate_twf_design,
            ValidateTwfInput(job_id="job-twf", source=VALID_DESIGN.replace("\t", "    ")),
        )

        assert result.valid
        assert result.source == VALID_DESIGN
        assert _path_from_uri(result.artifact_uris["twf_source"]).read_text() == VALID_DESIGN

    @pytest.mark.asyncio
    async def test_missing_twf_should_fail_naming_the_install_step(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Scenario: twf not installed
        Given `twf` does not resolve on PATH
        When the gate runs
        Then the activity fails, non-retryable, naming the install step
        """
        monkeypatch.setenv("PATH", "")

        with pytest.raises(ApplicationError, match="uv sync --extra architecture") as raised:
            await ActivityEnvironment().run(
                validate_twf_design, ValidateTwfInput(job_id="job-twf", source=VALID_DESIGN)
            )

        assert raised.value.non_retryable
