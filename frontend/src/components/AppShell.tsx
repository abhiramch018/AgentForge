import { NavLink, Outlet } from "react-router-dom";
import { useSession } from "../auth/SessionContext";
import { DemoBanner } from "./DemoBanner";

const NAV = [
  { to: "/", label: "Overview", end: true },
  { to: "/runs/new", label: "New run" },
  { to: "/approvals", label: "Approvals" },
  { to: "/memory", label: "Memory" },
  { to: "/history", label: "History" },
] as const;

export function AppShell() {
  const { meta, disconnect } = useSession();

  return (
    <div className="app-shell">
      <DemoBanner />
      <header className="app-header">
        <div className="brand">
          <span className="brand-mark" aria-hidden />
          <div>
            <p className="brand-name">AgentForge</p>
            <p className="brand-tag">Orchestration console</p>
          </div>
        </div>
        <nav className="app-nav" aria-label="Primary">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={"end" in item ? item.end : false}
              className={({ isActive }) =>
                isActive ? "nav-link active" : "nav-link"
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="header-meta">
          {meta ? (
            <span className="workspace-chip" title="Workspace scope">
              {meta.workspace_id}
            </span>
          ) : null}
          <button type="button" className="btn ghost" onClick={disconnect}>
            Disconnect
          </button>
        </div>
      </header>
      <main className="app-main">
        <Outlet />
      </main>
    </div>
  );
}
