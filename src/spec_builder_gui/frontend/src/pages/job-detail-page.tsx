import { startTransition, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { api } from "../api";
import { MarkdownPanel } from "../components/markdown-panel";
import { ReviewActions } from "../components/review-actions";
import { StatusBadge } from "../components/status-badge";
import { WorkflowTimeline } from "../components/workflow-timeline";
import type { JobDetail, SubmitReviewRequest } from "../types";

function formatTimestamp(timestamp: string) {
  return new Date(timestamp).toLocaleString();
}

export function JobDetailPage() {
  const { jobId = "" } = useParams();
  const navigate = useNavigate();
  const [detail, setDetail] = useState<JobDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [revisionFeedback, setRevisionFeedback] = useState("");
  const [revisionProjectName, setRevisionProjectName] = useState("");
  const [revisionPending, setRevisionPending] = useState(false);
  const [approveRevisionPending, setApproveRevisionPending] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let timeoutId: number | undefined;

    async function poll() {
      try {
        const nextDetail = await api.getJobDetail(jobId);
        if (cancelled) {
          return;
        }
        setDetail(nextDetail);
        setError(null);
        const interval =
          nextDetail.status === "queued" ||
          nextDetail.status === "running" ||
          nextDetail.status === "waiting_for_human"
            ? 3000
            : 15000;
        timeoutId = window.setTimeout(() => {
          if (document.visibilityState === "visible") {
            void poll();
          }
        }, interval);
      } catch (caughtError) {
        if (!cancelled) {
          setError(caughtError instanceof Error ? caughtError.message : "Job detail load failed.");
        }
      }
    }

    if (jobId) {
      void poll();
    }
    return () => {
      cancelled = true;
      if (timeoutId) {
        window.clearTimeout(timeoutId);
      }
    };
  }, [jobId]);

  async function handleReviewAction(payload: SubmitReviewRequest) {
    const nextDetail = await api.submitReview(jobId, payload);
    setDetail(nextDetail);
  }

  async function handleCreateRevision() {
    if (!detail) {
      return;
    }
    if (!revisionFeedback.trim()) {
      setError("Revision feedback is required.");
      return;
    }
    setRevisionPending(true);
    try {
      const created = await api.createRevision(jobId, {
        projectName: revisionProjectName.trim() || undefined,
        feedback: revisionFeedback.trim(),
      });
      startTransition(() => {
        navigate(`/jobs/${created.jobId}`);
      });
    } catch (caughtError) {
      setError(caughtError instanceof Error ? caughtError.message : "Revision launch failed.");
    } finally {
      setRevisionPending(false);
    }
  }

  async function handleApproveRevisionAsFinal() {
    setApproveRevisionPending(true);
    try {
      const nextDetail = await api.approveRevisionAsFinal(jobId);
      setDetail(nextDetail);
      setError(null);
    } catch (caughtError) {
      setError(
        caughtError instanceof Error ? caughtError.message : "Revision final approval failed.",
      );
    } finally {
      setApproveRevisionPending(false);
    }
  }

  if (error && !detail) {
    return (
      <section className="surface-card">
        <h1>Job Detail</h1>
        <p className="inline-error">{error}</p>
      </section>
    );
  }

  if (!detail) {
    return (
      <section className="surface-card">
        <h1>Job Detail</h1>
        <p className="muted-copy">Loading workflow detail...</p>
      </section>
    );
  }

  return (
    <div className="page-stack">
      <section className="hero-card compact-hero">
        <div>
          <p className="eyebrow">Now Reviewing</p>
          <h1>{detail.projectName}</h1>
          <p className="hero-copy">
            Workflow {detail.workflowId}
            {detail.runId ? ` · Run ${detail.runId}` : ""}
          </p>
        </div>
        <div className="detail-badge-cluster">
          <StatusBadge status={detail.status} />
          <span className="meta-pill">Phase: {detail.currentPhase.replace(/_/g, " ")}</span>
          <span className="meta-pill">Iteration {detail.iteration + 1}</span>
        </div>
      </section>

      {error ? <p className="inline-error">{error}</p> : null}

      <div className="page-grid detail-grid">
        <section className="surface-card">
          <div className="section-heading">
            <h2>Workflow State</h2>
            <p>Created {formatTimestamp(detail.createdAt)} · Updated {formatTimestamp(detail.updatedAt)}</p>
          </div>
          <dl className="detail-list">
            <div>
              <dt>Output Directory</dt>
              <dd>{detail.outputDirectory}</dd>
            </div>
            <div>
              <dt>Pending Human Action</dt>
              <dd>{detail.pendingHumanAction ?? "none"}</dd>
            </div>
            <div>
              <dt>Final Workflow Status</dt>
              <dd>{detail.finalWorkflowStatus ?? "open"}</dd>
            </div>
          </dl>
          <WorkflowTimeline stages={detail.stages} />
        </section>

        <section className="surface-card">
          <div className="section-heading">
            <h2>Artifacts & Review Notes</h2>
            <p>Everything tied to this job, including uploaded source material and human interventions.</p>
          </div>

          <ul className="artifact-list">
            {detail.artifacts.map((artifact) => (
              <li key={artifact.artifactId}>
                <strong>{artifact.filename}</strong>
                <span>{artifact.kind}</span>
              </li>
            ))}
          </ul>

          <h3>Open Questions</h3>
          {detail.openQuestions.length === 0 ? (
            <p className="muted-copy">No open questions are currently recorded.</p>
          ) : (
            <ul className="copy-list">
              {detail.openQuestions.map((question) => (
                <li key={question}>{question}</li>
              ))}
            </ul>
          )}

          <h3>Best-Practices Violations</h3>
          {detail.bestPracticesViolations.length === 0 ? (
            <p className="muted-copy">No review violations are currently surfaced.</p>
          ) : (
            <ul className="copy-list">
              {detail.bestPracticesViolations.map((violation) => (
                <li key={`${violation.severity}-${violation.issue}`}>
                  <strong>{violation.severity.toUpperCase()}</strong> {violation.issue}
                </li>
              ))}
            </ul>
          )}

          <h3>Review History</h3>
          {detail.reviewFeedbackHistory.length === 0 ? (
            <p className="muted-copy">No manual actions have been submitted yet.</p>
          ) : (
            <ul className="history-list">
              {detail.reviewFeedbackHistory.map((entry) => (
                <li key={entry.id}>
                  <strong>{entry.kind}</strong> · {formatTimestamp(entry.createdAt)}
                  {entry.text ? <p>{entry.text}</p> : null}
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <div className="page-grid detail-grid">
        <MarkdownPanel title="Current Draft" markdown={detail.currentDraftMarkdown} />
        <MarkdownPanel title="Final Spec" markdown={detail.finalSpecMarkdown} />
      </div>

      <ReviewActions detail={detail} onSubmit={handleReviewAction} />

      {detail.canCreateRevision || detail.canApproveRevisionAsFinal || detail.canDownload ? (
        <section className="surface-card">
          <div className="section-heading">
            <h2>{detail.parentJobId ? "Revision Output" : "Launch Revision"}</h2>
            <p>
              {detail.parentJobId
                ? "Approve this revision into the configured output folder, download the markdown, or launch another pass."
                : "Start a new workflow from this job’s current output plus revision notes."}
            </p>
          </div>
          {detail.canCreateRevision ? (
            <>
              <label className="field">
                <span>Revision Project Name</span>
                <input
                  value={revisionProjectName}
                  onChange={(event) => setRevisionProjectName(event.target.value)}
                  placeholder={`${detail.projectName} revision`}
                />
              </label>
              <label className="field">
                <span>Revision Feedback</span>
                <textarea
                  value={revisionFeedback}
                  onChange={(event) => setRevisionFeedback(event.target.value)}
                  rows={5}
                  placeholder="Describe the changes you want in the next workflow run."
                />
              </label>
            </>
          ) : null}
          {detail.canApproveRevisionAsFinal ? (
            <p className="muted-copy">
              Approving writes <code>spec.md</code> into <code>{detail.outputDirectory}</code>.
            </p>
          ) : null}
          <div className="inline-actions">
            {detail.canApproveRevisionAsFinal ? (
              <button
                type="button"
                className="primary-button"
                disabled={approveRevisionPending}
                onClick={() => void handleApproveRevisionAsFinal()}
              >
                {approveRevisionPending ? "Saving Final..." : "Approve Revision as Final"}
              </button>
            ) : null}
            {detail.canCreateRevision ? (
              <button
                type="button"
                className="secondary-button"
                disabled={revisionPending || approveRevisionPending}
                onClick={() => void handleCreateRevision()}
              >
                {revisionPending ? "Launching..." : "Create Revision"}
              </button>
            ) : null}
            {detail.canDownload ? (
              <a className="ghost-button" href={api.downloadUrl(detail.jobId)}>
                Download Final Markdown
              </a>
            ) : null}
          </div>
        </section>
      ) : null}
    </div>
  );
}
