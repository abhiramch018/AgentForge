import type { JobStatus } from "../api/types";

const LABELS: Record<JobStatus, string> = {
  queued: "Queued",
  running: "Running",
  succeeded: "Completed",
  failed: "Failed",
  interrupted: "Interrupted",
  awaiting_approval: "Awaiting approval",
};

export function statusLabel(status: JobStatus | string): string {
  return LABELS[status as JobStatus] ?? status;
}

export function StatusBadge({ status }: { status: JobStatus | string }) {
  const key = status.replace(/_/g, "-");
  return (
    <span className={`status-badge status-${key}`} title={status}>
      {statusLabel(status)}
    </span>
  );
}
