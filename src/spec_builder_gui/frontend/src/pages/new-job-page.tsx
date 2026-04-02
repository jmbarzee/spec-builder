import { startTransition, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../api";

type LocalUploadItem = {
  localId: string;
  artifactId?: string;
  filename: string;
  sizeBytes: number;
  kind?: string;
  state: "uploading" | "uploaded" | "error";
  error?: string;
};

const ACCEPTED_FILE_TYPES = ".pdf,.png,.jpg,.jpeg,.webp";

export function NewJobPage() {
  const navigate = useNavigate();
  const [projectName, setProjectName] = useState("");
  const [userIntent, setUserIntent] = useState("");
  const [outputDirectory, setOutputDirectory] = useState("");
  const [requireHumanApproval, setRequireHumanApproval] = useState(true);
  const [uploads, setUploads] = useState<LocalUploadItem[]>([]);
  const [formError, setFormError] = useState<string | null>(null);
  const [submitPending, setSubmitPending] = useState(false);
  const [directoryPicking, setDirectoryPicking] = useState(false);

  const uploadedArtifacts = useMemo(
    () => uploads.filter((upload) => upload.state === "uploaded" && upload.artifactId),
    [uploads],
  );

  async function handleFileSelection(fileList: FileList | null) {
    if (!fileList) {
      return;
    }
    setFormError(null);
    const files = Array.from(fileList);
    for (const file of files) {
      const localId = `${file.name}-${crypto.randomUUID()}`;
      setUploads((current) => [
        ...current,
        {
          localId,
          filename: file.name,
          sizeBytes: file.size,
          state: "uploading",
        },
      ]);
      try {
        const uploaded = await api.uploadArtifact(file);
        setUploads((current) =>
          current.map((entry) =>
            entry.localId === localId
              ? {
                  ...entry,
                  state: "uploaded",
                  artifactId: uploaded.artifactId,
                  kind: uploaded.kind,
                }
              : entry,
          ),
        );
      } catch (caughtError) {
        setUploads((current) =>
          current.map((entry) =>
            entry.localId === localId
              ? {
                  ...entry,
                  state: "error",
                  error:
                    caughtError instanceof Error
                      ? caughtError.message
                      : "Artifact upload failed.",
                }
              : entry,
          ),
        );
      }
    }
  }

  async function handlePickDirectory() {
    setDirectoryPicking(true);
    setFormError(null);
    try {
      const picked = await api.pickOutputDirectory();
      if (picked.path) {
        setOutputDirectory(picked.path);
      } else if (picked.supported && picked.cancelled) {
        setFormError("Directory selection was cancelled.");
      } else {
        setFormError("Native directory picking is not available on this machine.");
      }
    } catch (caughtError) {
      setFormError(caughtError instanceof Error ? caughtError.message : "Directory picker failed.");
    } finally {
      setDirectoryPicking(false);
    }
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedProjectName = projectName.trim();
    if (!trimmedProjectName) {
      setFormError("Project name is required.");
      return;
    }
    if (!outputDirectory.trim()) {
      setFormError("Output directory is required.");
      return;
    }
    if (uploadedArtifacts.length === 0) {
      setFormError("Upload at least one supported artifact before submitting.");
      return;
    }
    setSubmitPending(true);
    setFormError(null);
    try {
      const created = await api.createJob({
        projectName: trimmedProjectName,
        outputDirectory: outputDirectory.trim(),
        artifactIds: uploadedArtifacts.map((artifact) => artifact.artifactId!),
        userIntent: userIntent.trim() || undefined,
        requireHumanApproval,
        outputTargets: ["markdown_spec", "structured_json"],
      });
      startTransition(() => {
        navigate(`/jobs/${created.jobId}`);
      });
    } catch (caughtError) {
      setFormError(caughtError instanceof Error ? caughtError.message : "Job submission failed.");
    } finally {
      setSubmitPending(false);
    }
  }

  return (
    <div className="page-stack">
      <section className="hero-card">
        <div>
          <p className="eyebrow">Studio Session</p>
          <h1>Spin up a spec workspace.</h1>
          <p className="hero-copy">
            Upload PDFs and diagrams, point the workflow at a local folder, and track every
            iteration without dropping into the CLI.
          </p>
        </div>
        <div className="equalizer" aria-hidden="true">
          <span />
          <span />
          <span />
          <span />
        </div>
      </section>

      <form className="page-grid" onSubmit={handleSubmit}>
        <section className="surface-card">
          <div className="section-heading">
            <h2>New Job</h2>
            <p>Name the project, upload source files, and choose where the final spec lands.</p>
          </div>

          <label className="field">
            <span>Project Name</span>
            <input
              value={projectName}
              onChange={(event) => setProjectName(event.target.value)}
              placeholder="Playlist Transfer Spec"
            />
          </label>

          <label className="field">
            <span>User Intent</span>
            <textarea
              value={userIntent}
              onChange={(event) => setUserIntent(event.target.value)}
              rows={4}
              placeholder="Optional: describe the output you want the workflow to optimize for."
            />
          </label>

          <label className="field">
            <span>Artifacts</span>
            <input
              type="file"
              multiple
              accept={ACCEPTED_FILE_TYPES}
              onChange={(event) => void handleFileSelection(event.target.files)}
            />
            <small>Accepted types: PDF, PNG, JPG, JPEG, WEBP</small>
          </label>

          <label className="field">
            <span>Output Directory</span>
            <div className="inline-actions">
              <input
                value={outputDirectory}
                onChange={(event) => setOutputDirectory(event.target.value)}
                placeholder="/Users/you/spec-output"
              />
              <button
                type="button"
                className="secondary-button"
                onClick={() => void handlePickDirectory()}
                disabled={directoryPicking}
              >
                {directoryPicking ? "Picking..." : "Pick Folder"}
              </button>
            </div>
          </label>

          <label className="toggle-row">
            <input
              type="checkbox"
              checked={requireHumanApproval}
              onChange={(event) => setRequireHumanApproval(event.target.checked)}
            />
            <span>Require human approval before the workflow finalizes output.</span>
          </label>

          {formError ? <p className="inline-error">{formError}</p> : null}

          <button type="submit" className="primary-button" disabled={submitPending}>
            {submitPending ? "Launching..." : "Launch Spec Workflow"}
          </button>
        </section>

        <section className="surface-card">
          <div className="section-heading">
            <h2>Selected Files</h2>
            <p>Artifacts upload as soon as they are selected. Remove any item before launching.</p>
          </div>
          {uploads.length === 0 ? (
            <p className="muted-copy">No files uploaded yet.</p>
          ) : (
            <ul className="upload-list">
              {uploads.map((upload) => (
                <li key={upload.localId} className="upload-item">
                  <div>
                    <strong>{upload.filename}</strong>
                    <span>
                      {(upload.sizeBytes / 1024 / 1024).toFixed(2)} MiB
                      {upload.kind ? ` · ${upload.kind}` : ""}
                    </span>
                    {upload.error ? <p className="inline-error">{upload.error}</p> : null}
                  </div>
                  <div className="upload-meta">
                    <span className={`upload-state is-${upload.state}`}>{upload.state}</span>
                    <button
                      type="button"
                      className="ghost-button"
                      onClick={() =>
                        setUploads((current) =>
                          current.filter((entry) => entry.localId !== upload.localId),
                        )
                      }
                    >
                      Remove
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </form>
    </div>
  );
}
