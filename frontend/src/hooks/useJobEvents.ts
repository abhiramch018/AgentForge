import { useCallback, useEffect, useRef, useState } from "react";
import type { AgentForgeClient } from "../api/client";
import type { JobLifecycleEvent } from "../api/types";

export type StreamStatus = "idle" | "connecting" | "live" | "reconnecting" | "closed" | "error";

export function mergeLifecycleEvent(
  prev: JobLifecycleEvent[],
  incoming: JobLifecycleEvent,
): JobLifecycleEvent[] {
  const id = incoming.event_id;
  if (id != null && prev.some((e) => e.event_id === id)) {
    return prev;
  }
  return [...prev, incoming];
}

export function useJobEvents(
  client: AgentForgeClient,
  jobId: string | undefined,
  enabled: boolean,
) {
  const [events, setEvents] = useState<JobLifecycleEvent[]>([]);
  const [status, setStatus] = useState<StreamStatus>("idle");
  const [gapNotice, setGapNotice] = useState<string | null>(null);
  const lastIdRef = useRef<number | null>(null);
  const seenRef = useRef<Set<number>>(new Set());
  const abortRef = useRef<AbortController | null>(null);
  const bumpRef = useRef(0);

  const reset = useCallback(() => {
    setEvents([]);
    setGapNotice(null);
    lastIdRef.current = null;
    seenRef.current = new Set();
  }, []);

  useEffect(() => {
    if (!enabled || !jobId) {
      setStatus("idle");
      return;
    }
    let cancelled = false;
    let retry = 0;

    const connect = async () => {
      if (cancelled) return;
      abortRef.current?.abort();
      const ac = new AbortController();
      abortRef.current = ac;
      setStatus(retry === 0 ? "connecting" : "reconnecting");
      try {
        await client.openJobEventStream(jobId, {
          lastEventId: lastIdRef.current,
          signal: ac.signal,
          onEvent: ({ id, event, data }) => {
            if (cancelled) return;
            setStatus("live");
            retry = 0;
            if (event === "stream.replay_gap" || (data as JobLifecycleEvent)?.event_type === "stream.replay_gap") {
              const payload = data as { payload?: { message?: string }; message?: string };
              setGapNotice(
                payload.payload?.message ||
                  payload.message ||
                  "Event history gap — resynced from earliest retained event.",
              );
            }
            const numericId = id && /^\d+$/.test(id) ? Number(id) : null;
            const body = (typeof data === "object" && data !== null
              ? data
              : { event_type: event, payload: { raw: data } }) as JobLifecycleEvent;
            if (body.event_type == null && event) {
              body.event_type = event;
            }
            if (numericId != null) {
              body.event_id = numericId;
              if (seenRef.current.has(numericId)) return;
              seenRef.current.add(numericId);
              lastIdRef.current = numericId;
            } else if (typeof body.event_id === "number") {
              if (seenRef.current.has(body.event_id)) return;
              seenRef.current.add(body.event_id);
              lastIdRef.current = body.event_id;
            }
            // Batch into next paint without remounting the app shell.
            setEvents((prev) => mergeLifecycleEvent(prev, body));
            bumpRef.current += 1;
          },
        });
        if (!cancelled) setStatus("closed");
      } catch (err) {
        if (cancelled || (err as { name?: string }).name === "AbortError") return;
        setStatus("error");
        retry += 1;
        const delay = Math.min(8000, 500 * 2 ** Math.min(retry, 4));
        window.setTimeout(() => {
          if (!cancelled) void connect();
        }, delay);
      }
    };

    void connect();
    return () => {
      cancelled = true;
      abortRef.current?.abort();
    };
  }, [client, jobId, enabled]);

  return { events, status, gapNotice, reset, lastEventId: lastIdRef.current };
}
