import { useState, type FormEvent } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useSession } from "../auth/SessionContext";
import { DemoBanner } from "../components/DemoBanner";

export function ConnectPage() {
  const { connected, connect, connecting, error } = useSession();
  const location = useLocation();
  const from =
    (location.state as { from?: { pathname: string } } | null)?.from
      ?.pathname ?? "/";

  const [value, setValue] = useState("");
  const [remember, setRemember] = useState(false);

  if (connected) {
    return <Navigate to={from} replace />;
  }

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    void connect(value, remember);
  };

  return (
    <div className="connect-layout">
      <DemoBanner />
      <section className="connect-panel connect-card">
        <h1>Connect to AgentForge</h1>
        <p className="lede">
          Enter the orchestration bearer token. Tokens stay in memory for this
          session. Optionally keep them in sessionStorage for this browser tab
          only — never in the URL.
        </p>
        <form className="stack connect-form" onSubmit={onSubmit}>
          <label htmlFor="token">
            Bearer token
            <input
              id="token"
              type="password"
              autoComplete="off"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder="demo-token (if auth enabled)"
            />
          </label>
          <label className="check checkbox-row">
            <input
              type="checkbox"
              checked={remember}
              onChange={(e) => setRemember(e.target.checked)}
            />
            Remember for this browser tab (sessionStorage)
          </label>
          {error ? (
            <p className="error form-error" role="alert">
              {error}
            </p>
          ) : null}
          <button className="btn primary" type="submit" disabled={connecting}>
            {connecting ? "Connecting…" : "Connect"}
          </button>
        </form>
        <p className="muted tip fine-print">
          Validates <code className="mono">GET /health</code> and{" "}
          <code className="mono">GET /v1/meta</code> on the same origin (Compose
          port 8080, or Vite dev proxy to the API). Shared bearer token is
          shared-operator auth — not multi-user isolation.
        </p>
      </section>
    </div>
  );
}
