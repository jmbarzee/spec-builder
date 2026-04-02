"""Filesystem-backed storage for spec builder GUI metadata."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import unquote, urlparse

from src.spec_builder_gui.api_models import StoredJobRecord, UploadedArtifact

GUI_RUNTIME_ROOT = Path(".spec_builder_runtime/gui")


def path_to_uri(path: Path) -> str:
    """Convert a local path to a file URI."""
    return path.resolve().as_uri()


def uri_to_path(uri: str) -> Path:
    """Resolve a local file URI or raw path to a Path."""
    parsed = urlparse(uri)
    if parsed.scheme in ("", "file"):
        raw_path = parsed.path if parsed.scheme == "file" else uri
        return Path(unquote(raw_path)).expanduser().resolve()
    message = f"Only local file URIs are supported. Received: {uri}"
    raise ValueError(message)


class GuiJobStore:
    """Persist GUI artifacts and job records under `.spec_builder_runtime/gui/`."""

    def __init__(self, root: Path | str = GUI_RUNTIME_ROOT) -> None:
        self.root = Path(root)
        self.artifacts_dir = self.root / "artifacts"
        self.artifact_files_dir = self.artifacts_dir / "files"
        self.jobs_dir = self.root / "jobs"
        self.job_index_path = self.jobs_dir / "index.json"
        for path in (self.root, self.artifacts_dir, self.artifact_files_dir, self.jobs_dir):
            path.mkdir(parents=True, exist_ok=True)
        if not self.job_index_path.exists():
            self.job_index_path.write_text(json.dumps({"job_ids": []}, indent=2), encoding="utf-8")

    def artifact_record_path(self, artifact_id: str) -> Path:
        """Return the metadata path for one uploaded artifact."""
        return self.artifacts_dir / f"{artifact_id}.json"

    def artifact_file_path(self, artifact_id: str, filename: str) -> Path:
        """Return the storage path for one uploaded artifact file."""
        suffix = Path(filename).suffix
        return self.artifact_files_dir / f"{artifact_id}{suffix}"

    def save_artifact(self, artifact: UploadedArtifact) -> None:
        """Persist uploaded artifact metadata."""
        self.artifact_record_path(artifact.artifact_id).write_text(
            artifact.model_dump_json(indent=2, by_alias=True),
            encoding="utf-8",
        )

    def get_artifact(self, artifact_id: str) -> UploadedArtifact | None:
        """Load one uploaded artifact metadata record."""
        path = self.artifact_record_path(artifact_id)
        if not path.exists():
            return None
        return UploadedArtifact.model_validate_json(path.read_text(encoding="utf-8"))

    def get_artifacts(self, artifact_ids: list[str]) -> list[UploadedArtifact]:
        """Load multiple artifact records in order."""
        return [artifact for artifact_id in artifact_ids if (artifact := self.get_artifact(artifact_id))]

    def _write_job_index(self, job_ids: list[str]) -> None:
        self.job_index_path.write_text(
            json.dumps({"job_ids": job_ids}, indent=2),
            encoding="utf-8",
        )

    def _job_ids(self) -> list[str]:
        payload = json.loads(self.job_index_path.read_text(encoding="utf-8"))
        return list(payload.get("job_ids", []))

    def job_record_path(self, job_id: str) -> Path:
        """Return the metadata path for one job record."""
        return self.jobs_dir / f"{job_id}.json"

    def save_job(self, record: StoredJobRecord) -> None:
        """Persist one job record and update the index."""
        self.job_record_path(record.job_id).write_text(
            record.model_dump_json(indent=2, by_alias=True),
            encoding="utf-8",
        )
        job_ids = self._job_ids()
        if record.job_id not in job_ids:
            job_ids.append(record.job_id)
            self._write_job_index(job_ids)

    def get_job(self, job_id: str) -> StoredJobRecord | None:
        """Load one job record."""
        path = self.job_record_path(job_id)
        if not path.exists():
            return None
        return StoredJobRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def list_jobs(self) -> list[StoredJobRecord]:
        """Load all known job records."""
        records: list[StoredJobRecord] = []
        for job_id in self._job_ids():
            record = self.get_job(job_id)
            if record is not None:
                records.append(record)
        return records
