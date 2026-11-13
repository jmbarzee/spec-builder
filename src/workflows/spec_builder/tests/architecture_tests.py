"""Tests for architecture mode's setup: the pinned skills and the worker's startup check."""

from __future__ import annotations

import json
import re
import shutil
import tomllib
from pathlib import Path

import pytest

from src.workflows.spec_builder import architecture, worker
from src.workflows.spec_builder.architecture import (
    REPO_ROOT,
    SKILLS_DIR,
    ArchitectureSetupError,
    check_skills,
)


@pytest.fixture
def skills_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private copy of the skills package for a test to damage."""
    copy = tmp_path / "skills"
    shutil.copytree(SKILLS_DIR, copy)
    monkeypatch.setattr(architecture, "SKILLS_DIR", copy)
    return copy


def test_check_skills_should_pass_on_the_installed_package() -> None:
    """
    Scenario: Matching install
    Given `twf` and the skills package are installed at the same version
    When the startup check runs
    Then it passes
    """
    check_skills()


def test_check_skills_should_name_both_versions_when_they_differ(skills_copy: Path) -> None:
    """
    Scenario: Version mismatch
    Given a skills manifest from a different version than `twf`
    When the startup check runs
    Then it fails naming both versions
    """
    manifest = skills_copy / "MANIFEST.json"
    manifest.write_text(manifest.read_text().replace('"v0.17.0"', '"v0.16.0"', 1))

    with pytest.raises(ArchitectureSetupError, match=r"version 0\.17\.0.*version 0\.16\.0"):
        check_skills()


def test_check_skills_should_name_the_file_when_its_hash_does_not_match(skills_copy: Path) -> None:
    """
    Scenario: Changed skill file
    Given one byte of a skill file is changed
    When the startup check runs
    Then it fails naming that file
    """
    skill = skills_copy / "temporal-architect-design" / "SKILL.md"
    skill.write_bytes(skill.read_bytes() + b" ")

    with pytest.raises(ArchitectureSetupError, match="temporal-architect-design/SKILL.md"):
        check_skills()


def test_check_skills_should_name_the_file_when_it_is_missing(skills_copy: Path) -> None:
    """
    Scenario: Missing skill file
    Given a skill file is deleted
    When the startup check runs
    Then it fails naming that file
    """
    (skills_copy / "temporal-architect-design" / "reference" / "common-errors.md").unlink()

    with pytest.raises(ArchitectureSetupError, match=r"reference/common-errors\.md.*missing"):
        check_skills()


def test_skills_package_should_be_locked_at_the_twf_cli_version() -> None:
    """
    Scenario: One version
    Given the `architecture` extra pins `twf-cli`
    When the npm package and its lock are read
    Then both pin `@temporal-architect/claude-plugin` at that version
    """
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    [pin] = pyproject["project"]["optional-dependencies"]["architecture"]
    twf_version = re.fullmatch(r"twf-cli==(.+)", pin).group(1)
    package = json.loads((REPO_ROOT / "package.json").read_text())
    lock = json.loads((REPO_ROOT / "package-lock.json").read_text())

    assert package["dependencies"] == {"@temporal-architect/claude-plugin": twf_version}
    locked = lock["packages"]["node_modules/@temporal-architect/claude-plugin"]
    assert locked["version"] == twf_version


class _Connected(Exception):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize(("opted_in", "checked"), [("1", True), ("", False)])
async def test_worker_main_should_check_the_skills_before_connecting_only_when_opted_in(
    monkeypatch: pytest.MonkeyPatch, *, opted_in: str, checked: bool
) -> None:
    """
    Scenario: Startup check is opt-in
    Given a worker whose skills check fails
    When it starts with architecture mode on or off
    Then it fails before connecting when on, and connects without checking when off
    """
    monkeypatch.setenv("SPEC_BUILDER_ARCHITECTURE_MODE", opted_in)

    def bad_skills() -> None:
        raise ArchitectureSetupError("skills do not match")

    async def connect(*_args: object, **_kwargs: object) -> None:
        raise _Connected

    monkeypatch.setattr(worker, "check_skills", bad_skills)
    monkeypatch.setattr(worker.Client, "connect", connect)

    with pytest.raises(ArchitectureSetupError if checked else _Connected):
        await worker.main()
