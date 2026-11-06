# Design decisions and trade-offs

1. **Custom runtime over frameworks** — Explicit plan DAG, review, jobs, and
   approvals stay inspectable for a portfolio; no LangGraph/ADK dependency.

2. **SQLite + Huey on one host** — Enough for durable jobs and demos without
   operating Postgres/Redis. Trade-off: no multi-host scale; documented honestly.

3. **Jobs for HITL, not sync `/v1/runs`** — Approvals must not hold HTTP or a
   worker slot. Protected actions require `POST /v1/jobs`.

4. **Review ≠ approval** — Automatic reviewer is assignment-fit; human approval
   authorizes a constrained side effect (`write_artifact`).

5. **Lifecycle SSE, not token streaming** — Operators see plan/task/review/
   approval transitions from a durable log; reconnect via `Last-Event-ID`.

6. **HashingEmbedder default** — Deterministic offline memory for demos/tests;
   labeled as non-semantic so it is not mistaken for production RAG.

7. **Offline DemoLLM overlay** — Full stack demos without paid calls; scripted
   replies must be labeled as non-intelligence.

8. **Shared bearer token** — Simple for a single-operator demo; not multi-tenant
   security.
