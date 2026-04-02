import { useDeferredValue, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";
import { StatusBadge } from "../components/status-badge";
import type { JobStatus, JobSummary } from "../types";

type JobsPageProps = {
  finalOnly?: boolean;
};

function formatTimestamp(timestamp: string) {
  return new Date(timestamp).toLocaleString();
}

const FINAL_STATUSES: JobStatus[] = ["completed", "failed", "cancelled"];

export function JobsPage({ finalOnly = false }: JobsPageProps) {
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<JobStatus | "all">("all");
  const [error, setError] = useState<string | null>(null);
  const deferredSearch = useDeferredValue(search);

  useEffect(() => {
    let cancelled = false;
    let timeoutId: number | undefined;

    async function poll() {
      try {
        const response = await api.listJobs({
          search: deferredSearch || undefined,
          status:
            statusFilter !== "all" && !finalOnly
              ? statusFilter
              : undefined,
          pageSize: 50,
        });
        if (cancelled) {
          return;
        }
        const nextJobs = finalOnly
          ? response.items.filter((item) => FINAL_STATUSES.includes(item.status))
          : response.items;
        setJobs(nextJobs);
        setError(null);
        const hasActiveJobs = nextJobs.some((job) => !FINAL_STATUSES.includes(job.status));
        const interval = hasActiveJobs ? 3000 : 15000;
        timeoutId = window.setTimeout(() => {
          if (document.visibilityState === "visible") {
            void poll();
          }
        }, interval);
      } catch (caughtError) {
        if (!cancelled) {
          setError(caughtError instanceof Error ? caughtError.message : "Job refresh failed.");
          timeoutId = window.setTimeout(() => {
            if (document.visibilityState === "visible") {
              void poll();
            }
          }, 15000);
        }
      }
    }

    void poll();
    return () => {
      cancelled = true;
      if (timeoutId) {
        window.clearTimeout(timeoutId);
      }
    };
  }, [deferredSearch, finalOnly, statusFilter]);

  const visibleJobs = useMemo(
    () =>
      jobs
        .filter((job) => (finalOnly ? FINAL_STATUSES.includes(job.status) : true))
        .filter((job) => (statusFilter === "all" || finalOnly ? true : job.status === statusFilter)),
    [finalOnly, jobs, statusFilter],
  );

  return (
    <div className="page-stack">
      <section className="hero-card compact-hero">
        <div>
          <p className="eyebrow">{finalOnly ? "Archive" : "Live Board"}</p>
          <h1>{finalOnly ? "Past specs, ready to remix." : "Every workflow, on one board."}</h1>
          <p className="hero-copy">
            {finalOnly
              ? "Browse prior specs, reopen the markdown, and launch a revision when the draft needs another cut."
              : "Track running and waiting jobs with the same rhythm you’d expect from a polished local studio app."}
          </p>
        </div>
      </section>

      <section className="surface-card">
        <div className="toolbar">
          <label className="field compact-field">
            <span>Search</span>
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search by project name"
            />
          </label>
          <label className="field compact-field">
            <span>Status</span>
            <select
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value as JobStatus | "all")}
            >
              <option value="all">All statuses</option>
              <option value="queued">Queued</option>
              <option value="running">Running</option>
              <option value="waiting_for_human">Waiting</option>
              <option value="completed">Completed</option>
              <option value="failed">Failed</option>
              <option value="cancelled">Cancelled</option>
            </select>
          </label>
        </div>

        {error ? <p className="inline-error">{error}</p> : null}
        {visibleJobs.length === 0 ? (
          <p className="muted-copy">No jobs match the current filters.</p>
        ) : (
          <div className="job-grid">
            {visibleJobs.map((job) => (
              <article key={job.jobId} className="job-card">
                <div className="job-card-topline">
                  <StatusBadge status={job.status} />
                  <span className="muted-copy">Iteration {job.iteration + 1}</span>
                </div>
                <h2>{job.projectName}</h2>
                <p className="muted-copy">{job.currentPhase.replace(/_/g, " ")}</p>
                <dl className="job-meta">
                  <div>
                    <dt>Created</dt>
                    <dd>{formatTimestamp(job.createdAt)}</dd>
                  </div>
                  <div>
                    <dt>Updated</dt>
                    <dd>{formatTimestamp(job.updatedAt)}</dd>
                  </div>
                </dl>
                <div className="card-actions">
                  <Link className="primary-button" to={`/jobs/${job.jobId}`}>
                    Open
                  </Link>
                  {job.status === "completed" ? (
                    <a className="ghost-button" href={api.downloadUrl(job.jobId)}>
                      Download
                    </a>
                  ) : null}
                </div>
              </article>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
