import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import type { ApprovalRecord } from "../api/types";
import { useSession } from "../auth/SessionContext";
import { EmptyState } from "../components/EmptyState";
import { SafeMarkdown } from "../components/SafeMarkdown";

export function ApprovalsPage() {
  const { client } = useSession();
  const [items, setItems] = useState<ApprovalRecord[]>([]);
  const [selected, setSelected] = useState<ApprovalRecord | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [revisePath, setRevisePath] = useState("");
  const [reviseContent, setReviseContent] = useState("");
  const [reviseReason, setReviseReason] = useState("");

  const load = async () => {
    try {
      const res = await client.listApprovals();
      setItems(res.items);
      setError(null);
      if (selected) {
        const fresh = res.items.find((i) => i.approval_id === selected.approval_id);
        setSelected(fresh ?? res.items[0] ?? null);
      } else if (res.items[0]) {
        setSelected(res.items[0]);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load approvals");
    }
  };

  useEffect(() => {
    void load();
    const id = window.setInterval(() => void load(), 4000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client]);

  useEffect(() => {
    if (!selected) return;
    setRevisePath(selected.action.relative_path);
    setReviseContent(selected.action.content);
    setReviseReason(selected.action.reason);
  }, [selected]);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await load();
    } catch (err) {
      if (err instanceof ApiError) {
        setError(`${err.code}: ${err.message}`);
      } else {
        setError("Action failed");
      }
    } finally {
      setBusy(false);
    }
  };

  const onApprove = (e: FormEvent) => {
    e.preventDefault();
    if (!selected) return;
    // Bind to the exact current proposal fingerprint from the server.
    void act(() => client.approve(selected.approval_id, selected.fingerprint, note || undefined));
  };

  return (
    <section className="page">
      <header className="page-head">
        <div>
          <h1>Human approvals</h1>
          <p className="lede">
            Server-enforced proposals. Approve uses the <em>current</em>{" "}
            fingerprint only — stale versions conflict.
          </p>
        </div>
      </header>

      {error ? (
        <p className="error" role="alert">
          {error}
        </p>
      ) : null}

      {!items.length ? (
        <EmptyState
          title="No pending approvals"
          body="Protected actions pause jobs here until an operator decides."
        />
      ) : (
        <div className="split">
          <ul className="simple-list">
            {items.map((item) => (
              <li key={item.approval_id}>
                <button
                  type="button"
                  className={
                    selected?.approval_id === item.approval_id
                      ? "list-btn active"
                      : "list-btn"
                  }
                  onClick={() => setSelected(item)}
                >
                  <code>{item.approval_id.slice(0, 8)}…</code>
                  <span className="muted">job {item.job_id.slice(0, 8)}…</span>
                </button>
              </li>
            ))}
          </ul>

          {selected ? (
            <div className="panel stack">
              <p>
                Job{" "}
                <Link to={`/runs/${selected.job_id}`}>
                  <code>{selected.job_id}</code>
                </Link>
              </p>
              <p>
                <strong>Reason:</strong> {selected.reason}
              </p>
              <p>
                <strong>Action:</strong> {selected.action.action}
              </p>
              <p>
                <strong>Path:</strong> <code>{selected.action.relative_path}</code>
              </p>
              <p>
                <strong>Fingerprint:</strong>{" "}
                <code className="mono">{selected.fingerprint}</code>
              </p>
              {selected.risks?.length ? (
                <ul>
                  {selected.risks.map((r) => (
                    <li key={r}>{r}</li>
                  ))}
                </ul>
              ) : null}
              <div>
                <h3>Proposed content</h3>
                <SafeMarkdown content={selected.action.content} />
              </div>

              <form className="stack" onSubmit={onApprove}>
                <label>
                  Note (optional)
                  <input value={note} onChange={(e) => setNote(e.target.value)} />
                </label>
                <div className="btn-row">
                  <button className="btn primary" type="submit" disabled={busy}>
                    Approve current proposal
                  </button>
                  <button
                    className="btn danger"
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      void act(() =>
                        client.reject(selected.approval_id, note || undefined),
                      )
                    }
                  >
                    Reject
                  </button>
                </div>
              </form>

              <form
                className="stack inset"
                onSubmit={(e) => {
                  e.preventDefault();
                  void act(() =>
                    client.revise(selected.approval_id, {
                      action: "write_artifact",
                      relative_path: revisePath,
                      content: reviseContent,
                      reason: reviseReason,
                    }),
                  );
                }}
              >
                <h3>Revise proposal</h3>
                <p className="muted">
                  Creates a new fingerprint; prior approval cannot be reused.
                </p>
                <label>
                  Path
                  <input
                    value={revisePath}
                    onChange={(e) => setRevisePath(e.target.value)}
                    required
                  />
                </label>
                <label>
                  Reason
                  <input
                    value={reviseReason}
                    onChange={(e) => setReviseReason(e.target.value)}
                    required
                  />
                </label>
                <label>
                  Content
                  <textarea
                    rows={4}
                    value={reviseContent}
                    onChange={(e) => setReviseContent(e.target.value)}
                    required
                  />
                </label>
                <button className="btn" type="submit" disabled={busy}>
                  Submit revision
                </button>
              </form>
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
}
