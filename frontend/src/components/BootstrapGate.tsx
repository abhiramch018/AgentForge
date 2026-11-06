import { useEffect, useRef, useState, type ReactNode } from "react";
import { useSession } from "../auth/SessionContext";
import { LoadingScreen } from "./LoadingScreen";

/** Delay before showing the loader so instant bootstrap does not flash branding. */
const SHOW_DELAY_MS = 120;
const EXIT_MS = 220;

/**
 * Gates the app behind session bootstrap. Missing token → children immediately
 * (login route). Stored token → loader only while restore is in progress.
 */
export function BootstrapGate({ children }: { children: ReactNode }) {
  const {
    bootstrapping,
    bootstrapError,
    initStatus,
    retryBootstrap,
  } = useSession();

  const [visible, setVisible] = useState(false);
  const [exiting, setExiting] = useState(false);
  const showTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const exitTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const wasBlocking = useRef(false);

  const blocking = bootstrapping || Boolean(bootstrapError);

  useEffect(() => {
    if (blocking) {
      wasBlocking.current = true;
      setExiting(false);
      if (exitTimer.current) {
        clearTimeout(exitTimer.current);
        exitTimer.current = null;
      }
      if (!visible) {
        showTimer.current = setTimeout(() => {
          setVisible(true);
          showTimer.current = null;
        }, SHOW_DELAY_MS);
      }
      return () => {
        if (showTimer.current) {
          clearTimeout(showTimer.current);
          showTimer.current = null;
        }
      };
    }

    if (showTimer.current) {
      clearTimeout(showTimer.current);
      showTimer.current = null;
    }

    if (visible && wasBlocking.current) {
      setExiting(true);
      exitTimer.current = setTimeout(() => {
        setVisible(false);
        setExiting(false);
        wasBlocking.current = false;
        exitTimer.current = null;
      }, EXIT_MS);
      return () => {
        if (exitTimer.current) {
          clearTimeout(exitTimer.current);
          exitTimer.current = null;
        }
      };
    }

    setVisible(false);
    setExiting(false);
    wasBlocking.current = false;
    return undefined;
  }, [blocking, visible]);

  useEffect(() => {
    return () => {
      if (showTimer.current) clearTimeout(showTimer.current);
      if (exitTimer.current) clearTimeout(exitTimer.current);
    };
  }, []);

  const showApp = !bootstrapping && !bootstrapError;

  return (
    <>
      {visible ? (
        <LoadingScreen
          status={initStatus}
          error={bootstrapError}
          onRetry={bootstrapError ? () => void retryBootstrap() : undefined}
          exiting={exiting}
        />
      ) : null}
      {showApp ? children : null}
    </>
  );
}
