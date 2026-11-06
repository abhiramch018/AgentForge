import { useEffect, useState } from "react";
import { ApiError } from "../api/client";
import type { MemoryRecord } from "../api/types";
import { useSession } from "../auth/SessionContext";
import { EmptyState } from "../components/EmptyState";
import { SafeMarkdown } from "../components/SafeMarkdown";

export function MemoryPage() {
  const { client, meta } = useSession();
  const [items, setItems] = useState<MemoryRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      const res = await client.listMemory();
      setItems(res.items);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load memory");
    }
  };

  useEffect(() => {
    void load();
  }, [client]);

  return (
    <section className="page">
      <header className="page-head">
        <div>
          <h1>Memory</h1>
          <p className="lede">
            Workspace <code>{meta?.workspace_id}</code>. Retrieval uses{" "}
            <strong>HashingEmbedder</strong> (bag-of-tokens) —{" "}
            <em>not</em> production semantic memory quality.
          </p>
        </div>
        <button
          type="button"
          className="btn danger"
          disabled={busy || !items.length}
          onClick={() => {
            if (!window.confirm("Delete all memory in this workspace?")) return;
            setBusy(true);
            void client
              .deleteAllMemory()
              .then(load)
              .catch((err) =>
                setError(err instanceof ApiError ? err.message : "Delete failed"),
              )
              .finally(() => setBusy(false));
          }}
        >
          Delete all
        </button>
      </header>

      {error ? (
        <p className="error" role="alert">
          {error}
        </p>
      ) : null}

      {!items.length ? (
        <EmptyState
          title="No stored memories"
          body="Completed runs with memory enabled can write summaries here."
        />
      ) : (
        <ul className="memory-list">
          {items.map((m) => (
            <li key={m.memory_id} className="panel">
              <div className="row-between">
                <div>
                  <code>{m.memory_id.slice(0, 10)}…</code>
                  <span className="pill">{m.trust}</span>
                  <span className="pill">{m.kind}</span>
                </div>
                <button
                  type="button"
                  className="btn ghost"
                  disabled={busy}
                  onClick={() => {
                    setBusy(true);
                    void client
                      .deleteMemory(m.memory_id)
                      .then(load)
                      .catch((err) =>
                        setError(
                          err instanceof ApiError ? err.message : "Delete failed",
                        ),
                      )
                      .finally(() => setBusy(false));
                  }}
                >
                  Delete
                </button>
              </div>
              <SafeMarkdown content={m.summary} />
              <p className="muted">
                {new Date(m.created_at_ms).toLocaleString()}
              </p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
