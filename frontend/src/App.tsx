import type { ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { useSession } from "./auth/SessionContext";
import { BootstrapGate } from "./components/BootstrapGate";
import { Layout } from "./components/Layout";
import { ApprovalsPage } from "./pages/ApprovalsPage";
import { ConnectPage } from "./pages/ConnectPage";
import { HistoryPage } from "./pages/HistoryPage";
import { MemoryPage } from "./pages/MemoryPage";
import { NewRunPage } from "./pages/NewRunPage";
import { OverviewPage } from "./pages/OverviewPage";
import { RunDetailPage } from "./pages/RunDetailPage";

function RequireAuth({ children }: { children: ReactNode }) {
  const { connected, connecting } = useSession();
  // Initial restore is handled by BootstrapGate. Manual connect uses the
  // Connect page button state — do not trap users on an endless loader.
  if (connecting && !connected) {
    return null;
  }
  if (!connected) return <Navigate to="/connect" replace />;
  return children;
}

export default function App() {
  return (
    <BootstrapGate>
      <Routes>
        <Route path="/connect" element={<ConnectPage />} />
        <Route
          element={
            <RequireAuth>
              <Layout />
            </RequireAuth>
          }
        >
          <Route path="/" element={<OverviewPage />} />
          <Route path="/runs/new" element={<NewRunPage />} />
          <Route path="/runs/:jobId" element={<RunDetailPage />} />
          <Route path="/approvals" element={<ApprovalsPage />} />
          <Route path="/memory" element={<MemoryPage />} />
          <Route path="/history" element={<HistoryPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BootstrapGate>
  );
}
