# Configuration and troubleshooting

## Environment

Copy `.env.example` → `.env`. Required for Compose: `ORCHESTRATION_API_TOKEN`.  
Live (non-offline) profile also needs `LLM_API_KEY` / `LLM_MODEL` / `LLM_PROVIDER`.

Paths under Compose are forced to `/data/*` (shared volume). Do not point API and
worker at different hosts’ disk paths.

## Common issues

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Connect 401 | Wrong/missing bearer token | Use exact `ORCHESTRATION_API_TOKEN` from `.env` |
| `/ready` 503 | Volume permissions or missing `/data` | Check `agentforge-data` volume; api logs |
| Jobs stuck `queued` | Worker down / not offline when API is | `docker compose ps`; worker logs; offline overlay on **both** |
| No SSE updates | Proxy buffering / wrong origin | Use `:8080` same-origin; nginx sets `proxy_buffering off` |
| Approval 409 fingerprint | Stale UI / revised action | Refresh Approvals; approve current fingerprint |
| Artifact missing after “success” | Still awaiting_approval | Approve first; write happens on resume |
| Data lost after rebuild | Used `down -v` | Use `down` or `stop` to keep volume |
| “Live” quality expected in offline | DemoLLM scripted | Labeled offline — not intelligence evidence |

## Capacity / concurrency

Limits are **per process**. Scaling Compose `replicas` with shared SQLite is
unsupported. Raise `ORCHESTRATION_MAX_*` carefully on one API + one worker.

## Backup / restore

See `DEPLOY.md`. Always `compose stop` before restore. Prefer
`deploy/backup_sqlite.py` (SQLite online backup API).
