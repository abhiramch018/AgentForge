# AgentForge Dashboard (M14)

React + TypeScript UI for the real AgentForge API: submit jobs, watch authenticated SSE progress, handle approvals, inspect memory, latency, and cost estimates.

## Prerequisites

- Node.js 20+
- Backend demo API (fake LLMs, no paid calls)

## Start

Terminal 1 — demonstration API:

```powershell
cd ai-orchestration-platform
.\.venv\Scripts\python.exe -m examples.demo_api_server
```

Token: `demo-token` · URL: `http://127.0.0.1:8000`

Terminal 2 — dashboard (proxies `/v1` and `/health` to :8000):

```powershell
cd ai-orchestration-platform\frontend
npm install
$env:VITE_DEMO_MODE="true"
npm run dev
```

Open `http://127.0.0.1:5173`, connect with `demo-token`.

## Scripts

| Command | Purpose |
| --- | --- |
| `npm run dev` | Vite dev server + proxy |
| `npm run typecheck` | `tsc -b` |
| `npm run build` | Production build |
| `npm test` | Vitest unit tests |
| `npm run test:e2e` | Playwright against demo API + Vite |

## Auth

Bearer token is kept **in memory**. Optional “Remember for this browser tab” uses `sessionStorage` only. Tokens are never placed in URLs. Provider LLM keys are never bundled.

## Screens

Overview · New run · Run detail (plan/deps, SSE, outputs, review, cost) · Approvals · Memory · History

## Honesty labels

- Demonstration banner when `VITE_DEMO_MODE=true`
- Memory is HashingEmbedder — not production semantic retrieval
- Usage/cost show `complete` / `partial` / `unavailable`
- Execution success ≠ reviewer `quality_approved` ≠ human approval
