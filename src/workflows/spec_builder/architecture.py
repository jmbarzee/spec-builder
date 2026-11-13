"""Architecture mode: where the design skills live, and the startup check that they match `twf`."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

ARCHITECTURE_MODE_ENV = "SPEC_BUILDER_ARCHITECTURE_MODE"
REPO_ROOT = Path(__file__).resolve().parents[3]
SKILLS_DIR = REPO_ROOT / "node_modules" / "@temporal-architect" / "claude-plugin" / "skills"
INSTALL_HINT = "Run `npm ci` and `uv sync --extra architecture`."


class ArchitectureSetupError(RuntimeError):
    """Raised when `twf` and the design skills are missing or do not match."""


def architecture_mode_opted_in() -> bool:
    """Return whether this deployment opted in to architecture mode."""
    return os.environ.get(ARCHITECTURE_MODE_ENV, "").lower() in {"1", "true", "yes"}


def check_skills() -> None:
    """Fail unless `twf` and the skills package share a version and every skill file is intact."""
    twf = shutil.which("twf")
    if twf is None:
        raise ArchitectureSetupError(f"The `twf` CLI is not installed. {INSTALL_HINT}")
    manifest_path = SKILLS_DIR / "MANIFEST.json"
    if not manifest_path.is_file():
        raise ArchitectureSetupError(f"No skills manifest at {manifest_path}. {INSTALL_HINT}")

    twf_version = subprocess.run(  # noqa: S603 -- `twf` resolved from PATH above
        [twf, "--version"], capture_output=True, text=True, check=True
    ).stdout.split()[-1]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    skills_version = manifest["version"].removeprefix("v")
    if twf_version != skills_version:
        raise ArchitectureSetupError(
            f"`twf` is version {twf_version} but the skills are version {skills_version}. "
            f"{INSTALL_HINT}"
        )
    for entry in manifest["files"]:
        path = SKILLS_DIR / entry["path"]
        if not path.is_file():
            raise ArchitectureSetupError(f"Skill file {entry['path']} is missing. {INSTALL_HINT}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise ArchitectureSetupError(f"Skill file {entry['path']} has changed. {INSTALL_HINT}")
