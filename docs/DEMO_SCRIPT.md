# 3–5 minute portfolio demo script

**Mode:** offline Compose (DemoLLM). No paid API calls.  
**Label aloud:** “This uses a deterministic fake model so we can show the system paths — not model quality.”

## Setup (before the talk)

```powershell
cd ai-orchestration-platform
# .env with ORCHESTRATION_API_TOKEN set
docker compose -f docker-compose.yml -f docker-compose.offline.yml up -d
```

Open http://127.0.0.1:8080 — Connect with the bearer token from `.env`.

## Script (~4 minutes)

1. **Connect (15s)** — Paste token. Mention shared-token = shared operator, not multi-user auth.

2. **New run → Load portfolio demo preset (20s)** — Point out:
   - Fictional NordicCloud vs HarborStack briefs in the goal
   - Reviewer enabled (quality gate ≠ human auth)
   - Memory on (HashingEmbedder disclaimer)
   - Protected artifact write path

3. **Submit → Run detail (60–90s)** — Show live SSE lifecycle:
   - Planning completes with **3 tasks** (two analysts concurrent, then writer)
   - Review may show **needs_revision** then accept (bounded revision)
   - Status becomes **awaiting_approval** — capacity released while waiting

4. **Approvals (45s)** — Open Approvals. Show fingerprint-bound proposal. Approve.
   - Distinguish: reviewer acceptance ≠ human authorization ≠ “facts verified”
   - Job resumes; artifact written under `/data/artifacts/...`

5. **Result / observability (30s)** — Timing, usage, labeled cost **estimate**.  
   Optional: Replay timeline — “playback of recorded events, not a re-run.”

6. **Memory (20s)** — Memory page: rows labeled as hashing/offline retrieval.

7. **Close (15s)** — “Single-host SQLite; offline fake model; production would swap live LLM keys and keep the same job/approval boundaries.”

## Failure / recovery (optional 45s)

```powershell
python -m examples.portfolio_failure_recovery_demo
```

Explain: expired lease → `recover()` re-enqueues; `awaiting_approval` is not force-cancelled; LLM calls are not forcibly aborted on SIGTERM.

## Scripted CLI twin (no UI)

```powershell
python -m examples.portfolio_demo_offline
```

Expect `DEMO_OK` with three task ids and artifact write after approve.
