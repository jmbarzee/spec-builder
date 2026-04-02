import type { JobStatus } from "../types";

const STATUS_LABELS: Record<JobStatus, string> = {
  queued: "Queued",
  running: "Running",
  waiting_for_human: "Waiting",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
};

export function StatusBadge({ status }: { status: JobStatus }) {
  return (
    <span className={`status-badge status-${status.replace(/_/g, "-")}`}>{STATUS_LABELS[status]}</span>
  );
}
