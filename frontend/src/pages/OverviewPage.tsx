import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import type { JobListResponse } from "../api/types";
import { useSession } from "../auth/SessionContext";
import { EmptyState } from "../components/EmptyState";
import { StatusBadge } from "../components/StatusBadge";

export function OverviewPage() {
  const { client } = useSession();
  const [data, setData] = useState<JobListResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    client
      .listJobs(40)
      .then((res) => {
        if (alive) {
          setData(res);
          setError(null);
        }
      })
      .catch((err) => {
        if (!alive) return;
        setError(err instanceof ApiError ? err.message : "Failed to load jobs");
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [client]);

  const counts = data?.status_counts;

  return (
    <section className="page">
      <header className="page-head">
        <div>
          <h1>Overview</h1>
          <p className="lede">
            Live counts from the durable job store — not decorative placeholders.
          </p>
        </div>
        <Link className="btn primary" to="/runs/new">
          New run
        </Link>
      </header>

      {loading ? <p className="muted">Loading jobs…</p> : null}
      {error ? (
        <p className="error" role="alert">
          {error}
        </p>
      ) : null}

      {counts ? (
        <div className="metric-grid" aria-label="Job status counts">
          {(
            [
              ["queued", counts.queued],
              ["running", counts.running],
              ["awaiting_approval", counts.awaiting_approval],
              ["succeeded", counts.succeeded],
              ["failed", counts.failed],
              ["interrupted", counts.interrupted],
            ] as const
          ).map(([key, value]) => (
            <div key={key} className="metric">
              <StatusBadge status={key} />
              <strong>{value}</strong>
            </div>
          ))}
        </div>
      ) : null}

      {!loading && data && data.items.length === 0 ? (
        <EmptyState
          title="No jobs yet"
          body="Submit a run to see real status, latency, and cost from the API."
        />
      ) : null}

      {data && data.items.length > 0 ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Job</th>
                <th>Status</th>
                <th>Goal</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((job) => (
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
                    {new Date(job.updated_at_ms).toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
