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
  error: string | null;
  client: ApiClient;
  connect: (token: string, rememberTab: boolean) => Promise<void>;
  disconnect: () => void;
};

const SessionContext = createContext<SessionState | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const tokenRef = useRef<string | null>(getStoredToken());
  const [token, setToken] = useState<string | null>(tokenRef.current);
  const [meta, setMeta] = useState<MetaResponse | null>(null);
  const [connected, setConnected] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const client = useMemo(() => createClient(() => tokenRef.current), []);

  const connect = useCallback(
    async (nextToken: string, rememberTab: boolean) => {
      const cleaned = nextToken.trim();
      setConnecting(true);
      setError(null);
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
        setToken(cleaned || null);
        setMeta(m);
        setConnected(true);
      } catch (err) {
        tokenRef.current = null;
        setToken(null);
        setMeta(null);
        setConnected(false);
        clearSessionToken();
        if (err instanceof ApiError) {
          setError(
            err.status === 401
              ? "Unauthorized — check the bearer token."
              : err.message,
          );
        } else {
          setError("Backend unavailable. Start the API on :8000.");
        }
      } finally {
        setConnecting(false);
      }
    },
    [client],
  );

  const disconnect = useCallback(() => {
    tokenRef.current = null;
    setToken(null);
    setMeta(null);
    setConnected(false);
    setError(null);
    clearSessionToken();
  }, []);

  useEffect(() => {
    const existing = getStoredToken();
    if (existing) {
      void connect(existing, true).catch(() => {
        /* error state already set */
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const value = useMemo(
    (): SessionState => ({
      token,
      meta,
      connected,
      connecting,
      error,
      client,
      connect,
      disconnect,
    }),
    [token, meta, connected, connecting, error, client, connect, disconnect],
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
