# AgentForge architecture (deployed shape)

Single-host Docker Compose layout used for the portfolio demo. This matches
`docker-compose.yml` + optional `docker-compose.offline.yml` — not a multi-host
or Kubernetes topology.

```text
                    ┌─────────────────────────────────────┐
                    │  Host :8080 (only published port)   │
                    │  web = nginx + static React build   │
                    │  same-origin /v1 + /health + /ready │
                    │  proxy_buffering off for SSE        │
                    └───────────────┬─────────────────────┘
                                    │ Docker network
              ┌─────────────────────┴─────────────────────┐
              │                                           │
     ┌────────▼────────┐                        ┌─────────▼────────┐
     │  api (uvicorn)  │   enqueue job_id only  │ worker (Huey)    │
     │  FastAPI        │ ─────────────────────► │ consumes queue   │
     │  /v1/* SSE      │                        │ DemoLLM|OpenAI   │
     │  /ready checks  │ ◄── shared volume ───► │ /ready :8090     │
     └────────┬────────┘                        └─────────┬────────┘
              │                                           │
              └─────────────────┬─────────────────────────┘
                                │
                     ┌──────────▼──────────┐
                     │ volume: agentforge- │
                     │ data (/data)        │
                     │ jobs.db, huey_*.db  │
                     │ memory.db           │
                     │ approvals.db        │
                     │ artifacts/          │
                     └─────────────────────┘
```

## In-process engine (same binary as API/worker)

```text
goal → Supervisor.create_plan → ExecutionPlan (DAG)
     → PlanExecutor (ready tasks concurrent, ThreadPool)
         → AgentRunner specialists (tools disabled over HTTP)
         → optional OutputReviewer → bounded revise
     → optional ApprovalRequired (write_artifact) → pause job
     → on approve: write under artifact root
```

## Honest boundaries

| Concern | Reality |
|--------|---------|
| Auth | Shared bearer token = shared-operator, not multi-user isolation |
| Storage | SQLite+WAL on one host / one volume; do not scale API/worker replicas |
| Memory | HashingEmbedder lexical vectors — not production semantic search |
| Review | Assignment-fit under supplied context — not factual verification |
| Replay | Durable event timeline playback — not workflow re-execution |
| Cost | Fixture pricing estimates; missing usage ≠ zero |
| Offline demo | DemoLLM scripted replies — not live model intelligence |
| Shutdown | SIGTERM stops new work; does not forcibly cancel in-flight LLM HTTP |

See also `DEPLOY.md` for operations and `docs/READINESS.md` for verification status.
