# Portfolio materials (evidence-backed)

## Project description (short)

AgentForge is a custom multi-agent orchestration stack — planning, concurrent
specialist execution, optional review/revise, durable background jobs, memory,
human approval for artifact writes, live lifecycle streaming, and a React
operator dashboard — packaged for single-host Docker Compose. Offline demos use
a labeled fake LLM so the full control plane can be shown without paid API calls.

## Résumé bullets (verified only)

- Built a custom orchestration runtime (supervisor DAG planning, dependency-aware
  concurrent `PlanExecutor`, optional reviewer with bounded revisions) exposed
  as FastAPI jobs with a separate Huey worker and SQLite durability — not a
  LangGraph/ADK wrapper.
- Implemented human-in-the-loop pause/resume for fingerprint-bound artifact
  writes, authenticated SSE lifecycle streams, and HashingEmbedder workspace
  memory, with an offline Compose stack (API + worker + nginx) verified end-to-end.
- Authored an offline evaluation harness (40 case×config samples + 9/9 failure
  suite) with explicit mocked-vs-live labeling and fixture cost estimates; did
  not claim live model quality from mocked runs.

## Interview talking points

1. **Custom scheduler** — Ready tasks run concurrently in-process; dependents
   wait on succeeded upstream outputs; capacity gates are per process.
2. **Review loop** — Produce → structured review JSON → revise up to N; acceptance
   is assignment-fit under supplied evidence, not truth.
3. **Durable jobs** — `POST /v1/jobs` commits SQLite state before queue publish;
   queue carries only `job_id`; lease recovery re-enqueues expired running work.
4. **Approval boundary** — Separate from M6 review; only `write_artifact` under
   a constrained path; job releases worker capacity while awaiting humans.
5. **Concurrency limits** — `max_inflight_runs` / `max_inflight_tasks` /
   `max_run_concurrency` apply to one OS process, not a cluster.
6. **Latency/quality trade-offs** — Review off is faster; review on adds LLM
   calls (offline eval: higher P50 latency and model_calls when review enabled).
   Offline quality scores are **mocked** and must not be pitched as live accuracy.

## Future improvements (honest backlog)

- Provider embeddings / real vector retrieval
- Per-user identity beyond shared bearer token
- Model-token streaming (vs lifecycle SSE)
- Multi-host queue/store (leave SQLite single-host)
- True cancellation of in-flight provider HTTP
- Tools enabled for HTTP specialists (currently library-only)
- Live eval under explicit budgets (scaffolding refuses auto-paid runs)
