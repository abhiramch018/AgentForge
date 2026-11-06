import type { JobLifecycleEvent } from "../api/types";
import { formatTimestamp } from "../utils/format";

interface EventTimelineProps {
  events: JobLifecycleEvent[];
  streamStatus: string;
  replayGap: string | null;
}

export function EventTimeline({
  events,
  streamStatus,
  replayGap,
}: EventTimelineProps) {
  return (
    <section className="panel" aria-labelledby="events-heading">
      <div className="panel-head">
        <h2 id="events-heading">Lifecycle events</h2>
        <span className={`stream-pill stream-${streamStatus}`}>
          {streamStatus}
        </span>
      </div>
      {replayGap ? (
        <p className="notice-warn" role="status">
          {replayGap}
        </p>
      ) : null}
      {events.length === 0 ? (
        <p className="muted">Waiting for durable lifecycle events…</p>
      ) : (
        <ol className="event-timeline" aria-live="polite">
          {events.map((ev, idx) => {
            const id = ev.event_id ?? ev.id ?? idx;
            return (
              <li key={String(id)} className="event-row">
                <span className="mono event-id">#{String(id)}</span>
                <span className="event-type">{ev.event_type}</span>
                <time dateTime={String(ev.created_at_ms ?? "")}>
                  {formatTimestamp(ev.created_at_ms)}
                </time>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
