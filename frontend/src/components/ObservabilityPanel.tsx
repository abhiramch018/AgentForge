import type { ObservabilitySummary, UsageCoverage } from "../api/types";
import { formatMs } from "../utils/format";

interface ObservabilityPanelProps {
  data: ObservabilitySummary | null;
  loading: boolean;
  error: string | null;
}

function coverageLabel(c: UsageCoverage): string {
  if (c === "complete") return "Complete";
  if (c === "partial") return "Partial";
  return "Unavailable";
}

export function ObservabilityPanel({
  data,
  loading,
  error,
}: ObservabilityPanelProps) {
  return (
    <section className="panel" aria-labelledby="obs-heading">
      <h2 id="obs-heading">Usage &amp; cost</h2>
      {loading ? <p className="muted">Loading observability…</p> : null}
      {error ? <p className="state-inline-error">{error}</p> : null}
      {!data && !loading && !error ? (
        <p className="muted">
          Observability appears after the job stores a result.
        </p>
      ) : null}
      {data ? (
        <dl className="obs-grid">
          <div>
            <dt>Usage coverage</dt>
            <dd>{coverageLabel(data.usage.coverage)}</dd>
          </div>
          <div>
            <dt>Total tokens</dt>
            <dd>{data.usage.total_tokens ?? "—"}</dd>
          </div>
          <div>
            <dt>Cost (estimate)</dt>
            <dd>
              {data.cost.total_usd != null
                ? `$${data.cost.total_usd.toFixed(4)}`
                : "—"}{" "}
              <span className="muted">({data.cost.kind})</span>
            </dd>
          </div>
          <div>
            <dt>Cost coverage</dt>
            <dd>{coverageLabel(data.cost.coverage)}</dd>
          </div>
          {data.latency ? (
            <>
              <div>
                <dt>Queue wait</dt>
                <dd>{formatMs(data.latency.queue_wait_ms ?? null)}</dd>
              </div>
              <div>
                <dt>Planning</dt>
                <dd>{formatMs(data.latency.planning_ms ?? null)}</dd>
              </div>
              <div>
                <dt>Execution</dt>
                <dd>{formatMs(data.latency.execution_ms ?? null)}</dd>
              </div>
            </>
          ) : null}
        </dl>
      ) : null}
      {data?.cost.notes?.length ? (
        <ul className="notes-list">
          {data.cost.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
