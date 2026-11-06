# Evaluation summary (offline)

**Command run for this report:**

```powershell
python -m app.evaluation offline --out reports/eval_offline
```

**Actual result (2026-11-06):** `samples=40 failure_suite_passed=9/9`  
Artifacts: `reports/eval_offline/eval_report.json`, `eval_report.md`.

## Provenance

| Field | Value |
|-------|--------|
| Result kind | `mocked_offline` |
| Dataset | `v1` |
| Model identifier | `fake-model` |
| Paid calls | none |
| Sample count | 40 (case × config) |
| Failure suite | 9/9 passed |

## Config comparison (mocked — not live quality)

| Config | n | success_rate | quality_mean (mocked) | latency P50 (ms) | model_calls_sum |
|--------|---|--------------|------------------------|------------------|-----------------|
| review_off_memory_off | 10 | 1.0 | 1.0 | ~38 | 12 |
| review_on_memory_off | 10 | 1.0 | 1.0 | ~69 | 26 |
| review_off_memory_on | 10 | 1.0 | 1.0 | ~92 | 12 |
| review_on_memory_on | 10 | 1.0 | 1.0 | ~109 | 26 |

Latency and quality figures are from the **deterministic offline harness**. They
measure wiring and relative cost of review/memory configs under a fake model —
**not** production LLM accuracy or wall-clock performance on paid providers.

## Limitations (do not omit in portfolio talk)

- HashingEmbedder is lexical/offline, not semantic production retrieval.
- Mocked results ≠ live model quality.
- Small N; percentiles unstable.
- Cost totals are fixture estimates; respect usage/cost coverage fields.
- Prompt-injection cases prove boundary wiring, not live-model resistance.

Live evaluation exists as an opt-in CLI that **refuses** without
`--confirm-paid-calls` (not run for M16).
