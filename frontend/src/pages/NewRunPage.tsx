import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { useSession } from "../auth/SessionContext";
import {
  PORTFOLIO_ARTIFACT_CONTENT,
  PORTFOLIO_ARTIFACT_PATH,
  PORTFOLIO_GOAL,
} from "../demo/portfolioPreset";

export function NewRunPage() {
  const { client, meta } = useSession();
  const navigate = useNavigate();
  const [goal, setGoal] = useState("");
  const [review, setReview] = useState(false);
  const [memory, setMemory] = useState(meta?.memory_enabled_default ?? true);
  const [maxRevisions, setMaxRevisions] = useState(1);
  const [maxConcurrency, setMaxConcurrency] = useState(4);
  const [withApproval, setWithApproval] = useState(false);
  const [artifactPath, setArtifactPath] = useState("notes/demo.txt");
  const [artifactContent, setArtifactContent] = useState(
    "Demo artifact from AgentForge UI",
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadPortfolioPreset = () => {
    setGoal(PORTFOLIO_GOAL);
    setReview(true);
    setMemory(true);
    setMaxRevisions(1);
    setMaxConcurrency(4);
    setWithApproval(true);
    setArtifactPath(PORTFOLIO_ARTIFACT_PATH);
    setArtifactContent(PORTFOLIO_ARTIFACT_CONTENT);
    setError(null);
  };

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const body = {
        api_version: "v1" as const,
        goal: goal.trim(),
        review_enabled: review,
        memory_enabled: memory,
        max_revisions: maxRevisions,
        max_concurrency: maxConcurrency,
        ...(withApproval
          ? {
              protected_action: {
                action: "write_artifact" as const,
                relative_path: artifactPath.trim(),
                content: artifactContent,
                reason: "Dashboard-requested artifact write",
              },
            }
          : {}),
      };
      const res = await client.submitJob(body);
      navigate(`/runs/${res.job_id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Submit failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section className="page">
      <header className="page-head">
        <div>
          <h1>New run</h1>
          <p className="lede">
            Submits a durable background job via <code>POST /v1/jobs</code>.
            Disconnecting later will not cancel the job.
          </p>
        </div>
        <button
          type="button"
          className="btn"
          onClick={loadPortfolioPreset}
          title="Offline DemoLLM path — not live model intelligence"
        >
          Load portfolio demo preset
        </button>
      </header>

      <p className="hint">
        Portfolio preset uses the offline fake model when the stack is in
        DemoLLM mode. Reviewer acceptance is assignment-fit only — not factual
        verification. Artifact write still requires human approval.
      </p>

      <form className="stack form-card" onSubmit={onSubmit}>
        <label>
          Goal
          <textarea
            rows={8}
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            placeholder="Describe the work for AgentForge…"
            required
            maxLength={20000}
          />
        </label>
        <div className="row-opts">
          <label className="check">
            <input
              type="checkbox"
              checked={review}
              onChange={(e) => setReview(e.target.checked)}
            />
            Enable reviewer (quality gate — not human authorization)
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={memory}
              onChange={(e) => setMemory(e.target.checked)}
            />
            Enable memory (HashingEmbedder — not production semantic retrieval)
          </label>
        </div>
        <div className="row-opts">
          <label>
            Max revisions
            <input
              type="number"
              min={0}
              max={3}
              value={maxRevisions}
              onChange={(e) => setMaxRevisions(Number(e.target.value))}
            />
          </label>
          <label>
            Max concurrency
            <input
              type="number"
              min={1}
              max={32}
              value={maxConcurrency}
              onChange={(e) => setMaxConcurrency(Number(e.target.value))}
            />
          </label>
        </div>
        <label className="check">
          <input
            type="checkbox"
            checked={withApproval}
            onChange={(e) => setWithApproval(e.target.checked)}
          />
          Require human approval for a protected artifact write
        </label>
        {withApproval ? (
          <div className="stack inset">
            <label>
              Relative path (.txt / .md)
              <input
                value={artifactPath}
                onChange={(e) => setArtifactPath(e.target.value)}
                required={withApproval}
              />
            </label>
            <label>
              Content
              <textarea
                rows={5}
                value={artifactContent}
                onChange={(e) => setArtifactContent(e.target.value)}
                required={withApproval}
              />
            </label>
          </div>
        ) : null}
        {error ? (
          <p className="error" role="alert">
            {error}
          </p>
        ) : null}
        <button className="btn primary" type="submit" disabled={submitting}>
          {submitting ? "Submitting…" : "Submit job"}
        </button>
      </form>
    </section>
  );
}
