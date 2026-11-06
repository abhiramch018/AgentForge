import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useSession } from "../auth/SessionContext";

export function RequireAuth({ children }: { children: ReactNode }) {
  const { connected } = useSession();
  const location = useLocation();
  if (!connected) {
    return <Navigate to="/connect" replace state={{ from: location }} />;
  }
  return children;
}
