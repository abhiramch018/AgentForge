import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import type { JobListItem } from "../api/types";
import { useSession } from "../auth/SessionContext";
import { EmptyState } from "../components/EmptyState";
import { StatusBadge } from "../components/StatusBadge";

export function HistoryPage() {
  const { client } = useSession();
  const [items, setItems] = useState<JobListItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    client
      .listJobs(100)
      .then((res) => {
        setItems(
          res.items.filter(
            (j) => j.status === "succeeded" || j.status === "failed",
          ),
        );
        setError(null);
      })
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : "Failed to load history"),
      );
  }, [client]);

  return (
    <section className="page">
      <header className="page-head">
        <div>
          <h1>Results & history</h1>
          <p className="lede">
            Completed and failed jobs from the durable store. Open a run to
            inspect results and recorded event timelines.
          </p>
        </div>
      </header>
      {error ? (
        <p className="error" role="alert">
          {error}
        </p>
      ) : null}
      {!items.length && !error ? (
        <EmptyState title="No finished jobs" body="Successful or failed runs will appear here." />
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Job</th>
                <th>Status</th>
                <th>Goal</th>
                <th>Total</th>
              </tr>
            </thead>
            <tbody>
              {items.map((job) => (
                <tr key={job.job_id}>
                  <td>
                    <Link to={`/runs/${job.job_id}`}>
                      <code>{job.job_id.slice(0, 8)}…</code>
                    </Link>
                  </td>
                  <td>
                    <StatusBadge status={job.status} />
                  </td>
                  <td>{job.goal_preview ?? "—"}</td>
                  <td className="muted">
                    {job.timing.total_ms != null
                      ? `${job.timing.total_ms.toFixed(0)} ms`
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
