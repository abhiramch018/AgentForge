import "./LoadingScreen.css";

export type LoadingScreenProps = {
  status?: string;
  error?: string | null;
  onRetry?: () => void;
  exiting?: boolean;
};

/** Decorative infinity-ribbon mark; product name is in the wordmark. */
function InfinityMark() {
  return (
    <svg
      className="af-loader__svg"
      viewBox="0 0 96 96"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <linearGradient
          id="af-loader-grad"
          x1="12"
          y1="48"
          x2="84"
          y2="48"
          gradientUnits="userSpaceOnUse"
        >
          <stop offset="0%" stopColor="#22d3ee" />
          <stop offset="52%" stopColor="#3b82f6" />
          <stop offset="100%" stopColor="#8b5cf6" />
        </linearGradient>
      </defs>
      {/* Soft under-ribbon */}
      <path
        d="M18 48c0-11 9-20 22-20 7 0 12 3 18 9l4 4c6 6 11 9 18 9 13 0 22-9 22-20s-9-20-22-20c-7 0-12 3-18 9l-4 4c-6 6-11 9-18 9-13 0-22-9-22-20"
        fill="none"
        stroke="url(#af-loader-grad)"
        strokeWidth="8"
        strokeLinecap="round"
        strokeLinejoin="round"
        opacity="0.28"
      />
      {/* Primary interlocking infinity ribbon */}
      <path
        d="M16 48c0 12.5 10 22.5 24 22.5 7.5 0 13.5-3.2 20-9.7L66 54.3c5.5-5.5 10.5-8.3 16-8.3 12.5 0 22.5 10 22.5 22.5S94.5 91 82 91c-5.5 0-10.5-2.8-16-8.3L59.5 76.2C53 69.7 47 66.5 39.5 66.5 25.5 66.5 15.5 56.5 15.5 44"
        fill="none"
        stroke="url(#af-loader-grad)"
        strokeWidth="8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M80 48c0-12.5-10-22.5-24-22.5-7.5 0-13.5 3.2-20 9.7L30 41.7c-5.5 5.5-10.5 8.3-16 8.3C1.5 50 1.5 40 1.5 27.5S11.5 5 24 5c5.5 0 10.5 2.8 16 8.3l6.5 6.5C53 26.3 59 29.5 66.5 29.5 80.5 29.5 90.5 39.5 90.5 52"
        fill="none"
        stroke="url(#af-loader-grad)"
        strokeWidth="8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function LoadingScreen({
  status = "Loading workspace…",
  error = null,
  onRetry,
  exiting = false,
}: LoadingScreenProps) {
  return (
    <div
      className={`af-loader${exiting ? " is-exiting" : ""}`}
      role="presentation"
    >
      <div className="af-loader__inner">
        <div className="af-loader__mark">
          <div className="af-loader__glow" aria-hidden="true" />
          <InfinityMark />
        </div>
        <h1 className="af-loader__wordmark">AgentForge</h1>
        <p className="af-loader__subtitle">AI Orchestration Platform</p>
        {!error ? (
          <>
            <div className="af-loader__track" aria-hidden="true">
              <div className="af-loader__sweep" />
            </div>
            <p className="af-loader__status" role="status" aria-live="polite">
              {status}
            </p>
          </>
        ) : (
          <div className="af-loader__error">
            <p
              className="af-loader__error-text"
              role="alert"
              aria-live="assertive"
            >
              {error}
            </p>
            {onRetry ? (
              <button
                type="button"
                className="af-loader__retry"
                onClick={onRetry}
              >
                Retry
              </button>
            ) : null}
          </div>
        )}
      </div>
    </div>
  );
}
