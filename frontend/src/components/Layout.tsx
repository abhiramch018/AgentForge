import { NavLink, Outlet } from "react-router-dom";
import { useSession } from "../auth/SessionContext";
import { DemoBanner } from "./DemoBanner";

const links = [
  { to: "/", label: "Overview", end: true },
  { to: "/runs/new", label: "New run" },
  { to: "/approvals", label: "Approvals" },
  { to: "/memory", label: "Memory" },
  { to: "/history", label: "History" },
];

export function Layout() {
  const { token, meta, disconnect } = useSession();

  return (
    <div className="shell">
      <DemoBanner />
      <header className="topbar">
        <div className="brand-block">
          <p className="brand">AgentForge</p>
          <p className="brand-sub">Orchestration dashboard</p>
        </div>
        <nav className="nav" aria-label="Primary">
          {links.map((l) => (
            <NavLink
              key={l.to}
              to={l.to}
              end={l.end}
              className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}
            >
              {l.label}
            </NavLink>
          ))}
        </nav>
        <div className="session-chip">
          {token ? (
            <>
              <span className="muted">
                workspace <code>{meta?.workspace_id ?? "—"}</code>
              </span>
              <button type="button" className="btn ghost" onClick={disconnect}>
                Disconnect
              </button>
            </>
          ) : (
            <span className="muted">Not connected</span>
          )}
        </div>
      </header>
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
