import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { ApiClient, ApiError, createClient } from "../api/client";
import type { MetaResponse } from "../api/types";
import {
  clearSessionToken,
  getStoredToken,
  setSessionToken,
} from "../session/session";

type SessionState = {
  token: string | null;
  meta: MetaResponse | null;
  connected: boolean;
  connecting: boolean;
  /** True until the first stored-session restore attempt finishes (or is skipped). */
  bootstrapping: boolean;
  /** Status shown on the premium loader — only real operations. */
  initStatus: string;
  /** Fatal bootstrap failure (stored token restore). Login page uses `error`. */
  bootstrapError: string | null;
  error: string | null;
  client: ApiClient;
  connect: (token: string, rememberTab: boolean) => Promise<void>;
  disconnect: () => void;
  retryBootstrap: () => Promise<void>;
};

const SessionContext = createContext<SessionState | null>(null);

const DEFAULT_STATUS = "Loading workspace…";

function formatConnectError(err: unknown): string {
  if (err instanceof ApiError) {
    return err.status === 401
      ? "Unauthorized — check the bearer token."
      : err.message;
  }
  return "Backend unavailable. Start the API on :8000.";
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const tokenRef = useRef<string | null>(getStoredToken());
  const [token, setToken] = useState<string | null>(tokenRef.current);
  const [meta, setMeta] = useState<MetaResponse | null>(null);
  const [connected, setConnected] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [bootstrapping, setBootstrapping] = useState(() =>
    Boolean(getStoredToken()),
  );
  const [initStatus, setInitStatus] = useState(DEFAULT_STATUS);
  const [bootstrapError, setBootstrapError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const bootstrapGen = useRef(0);

  const client = useMemo(() => createClient(() => tokenRef.current), []);

  const applySession = useCallback((cleaned: string, m: MetaResponse) => {
    setToken(cleaned || null);
    setMeta(m);
    setConnected(true);
    setError(null);
    setBootstrapError(null);
  }, []);

  const clearSessionState = useCallback(() => {
    tokenRef.current = null;
    setToken(null);
    setMeta(null);
    setConnected(false);
    clearSessionToken();
  }, []);

  const restoreStoredSession = useCallback(async () => {
    const gen = ++bootstrapGen.current;
    const existing = getStoredToken();
    if (!existing) {
      setBootstrapping(false);
      setBootstrapError(null);
      setInitStatus(DEFAULT_STATUS);
      return;
    }

    setBootstrapping(true);
    setBootstrapError(null);
    setInitStatus(DEFAULT_STATUS);
    setConnecting(true);
    setSessionToken(existing, true);
    tokenRef.current = existing;

    try {
      setInitStatus("Checking API…");
      await client.health();
      if (gen !== bootstrapGen.current) return;
      setInitStatus(DEFAULT_STATUS);
      const m = await client.meta();
      if (gen !== bootstrapGen.current) return;
      if (m.auth_required && !existing.trim()) {
        throw new ApiError(
          401,
          "unauthorized",
          "Bearer token required for this API.",
        );
      }
      applySession(existing.trim(), m);
    } catch (err) {
      if (gen !== bootstrapGen.current) return;
      // Invalid/expired token → login, not an endless loader.
      if (err instanceof ApiError && err.status === 401) {
        clearSessionState();
        setError("Unauthorized — check the bearer token.");
        setBootstrapError(null);
      } else {
        // Keep token so Retry can re-attempt the same stored session.
        setMeta(null);
        setConnected(false);
        setBootstrapError(formatConnectError(err));
        setError(null);
      }
    } finally {
      if (gen === bootstrapGen.current) {
        setConnecting(false);
        setBootstrapping(false);
        setInitStatus(DEFAULT_STATUS);
      }
    }
  }, [applySession, clearSessionState, client]);

  const connect = useCallback(
    async (nextToken: string, rememberTab: boolean) => {
      const cleaned = nextToken.trim();
      setConnecting(true);
      setError(null);
      setBootstrapError(null);
      setSessionToken(cleaned, rememberTab);
      tokenRef.current = cleaned || null;
      try {
        await client.health();
        const m = await client.meta();
        if (m.auth_required && !cleaned) {
          throw new ApiError(
            401,
            "unauthorized",
            "Bearer token required for this API.",
          );
        }
        applySession(cleaned, m);
      } catch (err) {
        clearSessionState();
        setError(formatConnectError(err));
      } finally {
        setConnecting(false);
      }
    },
    [applySession, clearSessionState, client],
  );

  const disconnect = useCallback(() => {
    bootstrapGen.current += 1;
    clearSessionState();
    setError(null);
    setBootstrapError(null);
    setBootstrapping(false);
    setInitStatus(DEFAULT_STATUS);
  }, [clearSessionState]);

  const retryBootstrap = useCallback(async () => {
    await restoreStoredSession();
  }, [restoreStoredSession]);

  useEffect(() => {
    void restoreStoredSession();
    return () => {
      bootstrapGen.current += 1;
    };
  }, [restoreStoredSession]);

  const value = useMemo(
    (): SessionState => ({
      token,
      meta,
      connected,
      connecting,
      bootstrapping,
      initStatus,
      bootstrapError,
      error,
      client,
      connect,
      disconnect,
      retryBootstrap,
    }),
    [
      token,
      meta,
      connected,
      connecting,
      bootstrapping,
      initStatus,
      bootstrapError,
      error,
      client,
      connect,
      disconnect,
      retryBootstrap,
    ],
  );

  return (
    <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
  );
}

export function useSession(): SessionState {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession requires SessionProvider");
  return ctx;
}
