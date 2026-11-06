interface LoadingStateProps {
  label?: string;
}

export function LoadingState({ label = "Loading…" }: LoadingStateProps) {
  return (
    <div className="state-block" role="status" aria-live="polite">
      <div className="spinner" aria-hidden />
      <p>{label}</p>
    </div>
  );
}

interface EmptyStateProps {
  title: string;
  detail?: string;
}

export function EmptyState({ title, detail }: EmptyStateProps) {
  return (
    <div className="state-block state-empty">
      <h3>{title}</h3>
      {detail ? <p className="muted">{detail}</p> : null}
    </div>
  );
}

interface ErrorStateProps {
  title: string;
  detail?: string;
  onRetry?: () => void;
}

export function ErrorState({ title, detail, onRetry }: ErrorStateProps) {
  return (
    <div className="state-block state-error" role="alert">
      <h3>{title}</h3>
      {detail ? <p>{detail}</p> : null}
      {onRetry ? (
        <button type="button" className="btn secondary" onClick={onRetry}>
          Retry
        </button>
      ) : null}
    </div>
  );
}
