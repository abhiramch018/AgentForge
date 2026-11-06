# AgentForge deployment (M15)

Single-host Docker Compose packaging for the **real** architecture: production
frontend (nginx), FastAPI API, Huey worker, and a shared SQLite data volume.

This is **not** a multi-host or Kubernetes deployment. SQLite with WAL is
suitable for one API process + one worker process sharing one volume on one
machine. Do not scale `api`/`worker` replicas or mount the same DB across hosts.

## Auth boundary

- `ORCHESTRATION_API_TOKEN` is a **shared bearer token** for operators.
- It authenticates callers; it is **not** multi-user isolation.
- Memory and approvals are scoped to `ORCHESTRATION_WORKSPACE_ID` (one workspace).

Provider keys (`LLM_API_KEY`) and the service token stay in `.env` / runtime
environment — never in images, frontend bundles, or git.

## First-time setup

```bash
cd ai-orchestration-platform
cp .env.example .env
# Edit ORCHESTRATION_API_TOKEN (and LLM_* for live mode).
```

### Offline demonstration (recommended first run — no paid API calls)

Labeled overlay uses `DemoLLM` while still running API + worker + persistence + SSE:

```bash
docker compose -f docker-compose.yml -f docker-compose.offline.yml up --build -d
```

Open **http://127.0.0.1:8080**, paste the bearer token from `.env`.

Verify:

```bash
curl -s http://127.0.0.1:8080/ready
# optional end-to-end probe (uses token from env):
set ORCHESTRATION_API_TOKEN=...   # PowerShell: $env:ORCHESTRATION_API_TOKEN=...
python deploy/verify_compose_stack.py
```

### Live providers (paid calls possible)

```bash
# Ensure LLM_API_KEY and LLM_MODEL are set in .env
docker compose up --build -d
```

Only host port **8080** is published (UI + proxied API). API `:8000` and worker
health `:8090` stay on the Docker network.

## Everyday operations

| Action | Command |
|--------|---------|
| Build & start | `docker compose up --build -d` (add offline overlay files as above) |
| Logs | `docker compose logs -f api worker web` |
| Stop (keep data) | `docker compose stop` or `docker compose down` |
| Restart | `docker compose restart` |
| Rebuild containers only | `docker compose up --build -d` — **volume data is kept** |
| Destroy containers **and** volume | `docker compose down -v` ← **destructive** |

`docker compose down` removes containers/networks but **keeps** the named volume
`agentforge-data`. `docker compose down -v` deletes job/memory/approval history.

## Health vs readiness

- `GET /health` — process liveness (API or nginx→API).
- `GET /ready` — SQLite stores + artifact root usable; API returns 503 if not.
- Worker: `http://worker:8090/ready` (internal) requires consumer started + DBs.

Compose `healthcheck`s use `/ready`, not mere process existence.

## Shutdown / restart behavior

- Stopping the worker stops accepting **new** Huey deliveries.
- An **in-flight LLM HTTP call is not forcibly cancelled**; it may finish or time out.
- On restart, lease recovery re-enqueues `running` jobs with expired leases and
  `queued` jobs needing delivery.
- Jobs in `awaiting_approval` stay paused (no worker lease) until approve/reject/revise.
- API lifespan also runs recovery on startup.

## Backup (SQLite-safe)

Prefer the Python online backup API (consistent copy without copying live `-wal` files alone):

```bash
mkdir -p backups
docker compose run --rm \
  -v "${PWD}:/src" -v "${PWD}/backups:/backup" -w /src \
  api python deploy/backup_sqlite.py --data-dir /data --out-dir /backup/snap1
```

PowerShell:

```powershell
New-Item -ItemType Directory -Force backups | Out-Null
docker compose run --rm `
  -v "${PWD}:/src" -v "${PWD}/backups:/backup" -w /src `
  api python deploy/backup_sqlite.py --data-dir /data --out-dir /backup/snap1
```

## Restore

Stop writers, restore, start:

```bash
docker compose stop api worker web
docker compose run --rm \
  -v "${PWD}:/src" -v "${PWD}/backups/snap1:/backup" -w /src \
  api python deploy/restore_sqlite.py --backup-dir /backup --data-dir /data --yes
docker compose start api worker web
```

Verify restoration by listing jobs / memory via the UI or `GET /v1/jobs`.

## Resource expectations (single host)

| Component | Rough guidance |
|-----------|----------------|
| API | 256–512 MiB RAM idle; more under concurrent `/v1/runs` |
| Worker | 256–512 MiB; LLM latency dominates CPU wait |
| Web/nginx | ~32–64 MiB |
| Volume | Grows with jobs, events, memory, artifacts |

Disk: keep free space for SQLite WAL; back up before upgrades.

## Configuration limits (summary)

1. Single host; shared volume; one API + one worker.
2. Shared token ≠ per-user security.
3. Offline compose is labeled demo mode (fake models).
4. Live mode requires valid `LLM_*` secrets at runtime.
5. No Kubernetes / service mesh in this milestone.

## Networking / SSE

nginx terminates port 8080 and reverse-proxies `/v1/` to the API with
`proxy_buffering off` so authenticated lifecycle SSE is not held in proxy buffers.
The SPA uses same-origin relative `/v1/...` paths (no API URL baked into the bundle).
