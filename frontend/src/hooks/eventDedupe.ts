import type { JobLifecycleEvent } from "../api/types";

export function eventKey(ev: JobLifecycleEvent): string {
  if (ev.event_id != null) {
    return String(ev.event_id);
  }
  if (ev.id != null) {
    return String(ev.id);
  }
  return `${ev.event_type}:${ev.created_at_ms ?? 0}`;
}

export function mergeEvents(
  existing: JobLifecycleEvent[],
  incoming: JobLifecycleEvent[],
): JobLifecycleEvent[] {
  const map = new Map<string, JobLifecycleEvent>();
  for (const ev of existing) {
    map.set(eventKey(ev), ev);
  }
  for (const ev of incoming) {
    map.set(eventKey(ev), ev);
  }
  return [...map.values()].sort((a, b) => {
    const ai = a.event_id ?? Number(a.id) ?? 0;
    const bi = b.event_id ?? Number(b.id) ?? 0;
    return ai - bi;
  });
}
