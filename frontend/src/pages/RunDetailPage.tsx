import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError } from "../api/client";
import type {
  JobResultResponse,
  JobStatusResponse,
  ObservabilitySummary,
  ReplayTimeline,
  RunResult,
} from "../api/types";
import { useSession } from "../auth/SessionContext";
import { DependencyList } from "../components/DependencyList";
import { SafeMarkdown } from "../components/SafeMarkdown";
import { StatusBadge } from "../components/StatusBadge";
import { useJobEvents } from "../hooks/useJobEvents";

export function RunDetailPage() {
  const { jobId } = useParams();
  const { client } = useSession();
  const [status, setStatus] = useState<JobStatusResponse | null>(null);
  const [result, setResult] = useState<RunResult | null>(null);
  const [obs, setObs] = useState<ObservabilitySummary | null>(null);
  const [replay, setReplay] = useState<ReplayTimeline | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const active =
    status?.status === "queued" ||
    status?.status === "running" ||
    status?.status === "interrupted" ||
    status?.status === "awaiting_approval";

  const { events, status: streamStatus, gapNotice } = useJobEvents(
    client,
    jobId,
    Boolean(jobId) && (active || loading),
  );

  const refresh = useCallback(async () => {
    if (!jobId) return;
    try {
      const st = await client.getJob(jobId);
      setStatus(st);
      setError(null);
      if (st.result_available || st.status === "succeeded" || st.status === "failed" || st.status === "awaiting_approval") {
        try {
          const res: JobResultResponse = await client.getJobResult(jobId);
          setResult(res.result);
        } catch (err) {
          if (!(err instanceof ApiError && err.status === 409)) {
            /* awaiting may 409 until partial stored */
          }
        }
        try {
          setObs(await client.getObservability(jobId));
        } catch {
          setObs(null);
        }
        try {
          setReplay(await client.getReplay(jobId));
        } catch {
          setReplay(null);
        }
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load job");
    } finally {
      setLoading(false);
    }
  }, [client, jobId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => {
      void refresh();
    }, 2000);
    return () => window.clearInterval(id);
  }, [active, refresh]);

  // Resync when stream reports terminal events without waiting for poll.
  useEffect(() => {
    const last = events[events.length - 1];
    if (!last) return;
    if (
      last.event_type === "job.succeeded" ||
      last.event_type === "job.failed" ||
      last.event_type === "approval.waiting"
    ) {
      void refresh();
    }
  }, [events, refresh]);

  if (!jobId) {
    return <p className="error">Missing job id</p>;
  }

  return (
    <section className="page">
      <header className="page-head">
        <div>
          <p className="eyebrow">
            <Link to="/">Overview</Link> / run
          </p>
          <h1>
            Job <code className="mono">{jobId}</code>
          </h1>
          <p className="lede">
            Live lifecycle stream uses authenticated fetch SSE. Leaving this
            page does not cancel the job.
          </p>
        </div>
        {status ? <StatusBadge status={status.status} /> : null}
      </header>

      {loading ? <p className="muted">Loading…</p> : null}
      {error ? (
        <p className="error" role="alert">
          {error}
        </p>
      ) : null}

      {status ? (
        <div className="info-strip">
          <span>
            Attempt {status.attempt}/{status.max_attempts}
          </span>
          <span>
            Stream: <strong>{streamStatus}</strong>
          </span>
          {status.uncertainty ? (
            <span className="warn">Uncertainty: {status.uncertainty}</span>
          ) : null}
          {status.error_message ? (
            <span className="error">{status.error_message}</span>
          ) : null}
        </div>
      ) : null}

      {gapNotice ? (
        <p className="warn" role="status">
          {gapNotice}
        </p>
      ) : null}

      <div className="split">
        <section className="panel">
          <h2>Plan & dependencies</h2>
          {result?.plan ? (
            <>
              <p>
                <strong>Goal:</strong> {result.plan.goal}
              </p>
              <DependencyList tasks={result.plan.tasks} />
            </>
          ) : (
            <p className="muted">
              Plan appears when a result/checkpoint is available.
            </p>
          )}
        </section>

        <section className="panel">
          <h2>Live events</h2>
          <ol className="event-list">
            {events.map((ev, idx) => (
              <li key={ev.event_id ?? `${ev.event_type}-${idx}`}>
                <code>{ev.event_type}</code>
                {ev.event_id != null ? (
                  <span className="muted"> #{ev.event_id}</span>
                ) : null}
              </li>
            ))}
          </ol>
          {!events.length ? (
            <p className="muted">Waiting for lifecycle events…</p>
          ) : null}
        </section>
      </div>

      <section className="panel">
        <h2>Tasks</h2>
        <TruthLegend />
        {result?.execution?.task_results?.length ? (
          <div className="task-stack">
            {result.execution.task_results.map((task) => (
              <article key={task.task_id} className="task-card">
                <header>
                  <code>{task.task_id}</code>
                  <StatusBadge status={task.status} />
                  <span className="pill">
                    quality_approved={String(task.quality_approved)}
                  </span>
                  <span className="muted">{task.review_policy}</span>
                </header>
                {task.error ? <p className="error">{task.error}</p> : null}
                {task.output ? <SafeMarkdown content={task.output} /> : null}
                {task.attempts?.map((a) =>
                  a.review ? (
                    <div key={a.index} className="review-box">
                      <strong>Review ({a.review.decision})</strong>
                      <p>{a.review.feedback}</p>
                      {a.review.issues?.length ? (
                        <ul>
                          {a.review.issues.map((i) => (
                            <li key={i}>{i}</li>
                          ))}
                        </ul>
                      ) : null}
                    </div>
                  ) : null,
                )}
                <p className="muted">
                  Revisions: {task.revision_count}
                  {task.duration_ms != null
                    ? ` · ${task.duration_ms.toFixed(0)} ms`
                    : ""}
                </p>
              </article>
            ))}
          </div>
        ) : (
          <p className="muted">No task results yet.</p>
        )}
      </section>

      <section className="panel">
        <h2>Timing & cost</h2>
        <dl className="kv">
          <div>
            <dt>Queue wait</dt>
            <dd>{fmtMs(status?.timing.queue_wait_ms)}</dd>
          </div>
          <div>
            <dt>Execute</dt>
            <dd>{fmtMs(status?.timing.execute_ms)}</dd>
          </div>
          <div>
            <dt>Total</dt>
            <dd>{fmtMs(status?.timing.total_ms)}</dd>
          </div>
          <div>
            <dt>Usage coverage</dt>
            <dd>{obs?.usage.coverage ?? "unavailable"}</dd>
          </div>
          <div>
            <dt>Tokens</dt>
            <dd>
              {obs?.usage.total_tokens != null
                ? obs.usage.total_tokens
                : "unavailable"}
            </dd>
          </div>
          <div>
            <dt>Cost estimate</dt>
            <dd>
              {obs?.cost.coverage === "unavailable" || obs?.cost.total_usd == null
                ? `${obs?.cost.coverage ?? "unavailable"} (not zero-filled)`
                : `$${obs.cost.total_usd.toFixed(6)} (${obs.cost.coverage} estimate)`}
            </dd>
          </div>
        </dl>
        {obs?.cost.notes?.length ? (
          <ul className="notes">
            {obs.cost.notes.slice(0, 4).map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        ) : null}
      </section>

      {replay ? (
        <section className="panel">
          <h2>Recorded timeline (playback)</h2>
          <p className="muted">
            Mode <code>{replay.mode}</code> — not a workflow re-execution.
          </p>
          <p>{replay.event_count} events retained.</p>
        </section>
      ) : null}

      {status?.status === "awaiting_approval" ? (
        <p className="warn">
          Human authorization required.{" "}
          <Link to="/approvals">Open approvals</Link>
        </p>
      ) : null}
    </section>
  );
}

function TruthLegend() {
  return (
    <p className="muted legend">
      <strong>Succeeded</strong> = execution finished.{" "}
      <strong>quality_approved</strong> = reviewer acceptance (not human auth).{" "}
      <strong>Awaiting approval</strong> = protected side effect gate.
    </p>
  );
}

function fmtMs(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${v.toFixed(0)} ms`;
}
