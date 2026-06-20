---
branch: chore/code-review-update
initial_review_commit: 23c29e1
last_updated_commit: a1b11b2
last_staleness_scan:
  commit: a1b11b2
  date: 2026-06-19
---

# Code Review Summary

## Per-theme grades

| Theme | Grade | Stories (open/in-progress/stale) |
|---|---|---|
| CORR | A | 0 critical, 0 high, 1 medium, 2 low |
| ML | A | 0 critical, 0 high, 0 medium, 0 low |
| MATH | A | 0 critical, 0 high, 0 medium, 0 low |
| ARCH | A | 0 critical, 0 high, 0 medium, 2 low |
| CFG | A | 0 critical, 0 high, 1 medium, 1 low |
| MAINT | A | 0 critical, 0 high, 1 medium, 1 low |
| EXC | A | 0 critical, 0 high, 1 medium, 1 low |
| TYPE | A | 0 critical, 0 high, 2 medium, 0 low |
| TEST | B | 0 critical, 0 high, 3 medium, 1 low |
| REPRO | A | 0 critical, 0 high, 1 medium, 1 low |
| DATA | B | 0 critical, 0 high, 4 medium, 0 low |
| PERF | B | 0 critical, 0 high, 3 medium, 0 low |
| OBS | A | 0 critical, 0 high, 1 medium, 3 low |
| SEC | B | 0 critical, 0 high, 3 medium, 2 low |
| DX | A | 0 critical, 0 high, 1 medium, 0 low |
| PKG | A | 0 critical, 0 high, 0 medium, 1 low |
| DOC | A | 0 critical, 0 high, 1 medium, 1 low |

## Top concerns

Stories sorted by severity desc, then ID asc, top 10 (or all if fewer than 10). Each entry cites the bundle file the story lives in.

1. CFG-001 — ACHERON_STORE_BACKEND / REDIS_URL selection logic duplicated across create_worker_store and create_job_store [medium] — `architecture.md`
2. CORR-008 — StreamingExecutor loses cost accounting when handler returns non-SUCCESS status [medium] — `correctness.md`
3. DATA-001 — API pydantic schemas accept arbitrary extra fields, silently dropping client typos [medium] — `verification.md`
4. DATA-002 — Redis deserialization corruption handling inconsistent — _deserialize_job and _deserialize_worker metadata raise raw JSONDecodeError [medium] — `verification.md`
5. DATA-003 — Redis store round-trip gaps: PlanStep.batch=True and non-empty metadata untested [medium] — `verification.md`
6. DATA-004 — Redis store round-trip tests never exercise non-empty worker metadata, leaving a coverage gap for real production values [medium] — `verification.md`
7. DOC-002 — README architecture tree references removed BatchAsync strategy [medium] — `surface.md`
8. DX-001 — Quick Start omits `just certs` — fresh clone breaks `docker compose up` [medium] — `surface.md`
9. EXC-001 — tenacity dependency is unused; WorkerTimeoutError/PlanValidationError are never raised; transient network calls have no retry [medium] — `code-quality.md`
10. MAINT-002 — redis.py hand-rolls JSON ser/deser for domain models that cache.py serializes via pydantic, duplicating and drifting [medium] — `code-quality.md`

## Quick wins

Stories with `effort: S` AND `severity ∈ {medium, high, critical}` AND `status ∈ {open, in-progress, stale}`:

1. CFG-001 — ACHERON_STORE_BACKEND / REDIS_URL selection logic duplicated across create_worker_store and create_job_store [medium, S] — `architecture.md`
2. CORR-008 — StreamingExecutor loses cost accounting when handler returns non-SUCCESS status [medium, S] — `correctness.md`
3. DATA-001 — API pydantic schemas accept arbitrary extra fields, silently dropping client typos [medium, S] — `verification.md`
4. DATA-002 — Redis deserialization corruption handling inconsistent — _deserialize_job and _deserialize_worker metadata raise raw JSONDecodeError [medium, S] — `verification.md`
5. DATA-003 — Redis store round-trip gaps: PlanStep.batch=True and non-empty metadata untested [medium, S] — `verification.md`
6. DATA-004 — Redis store round-trip tests never exercise non-empty worker metadata, leaving a coverage gap for real production values [medium, S] — `verification.md`
7. DOC-002 — README architecture tree references removed BatchAsync strategy [medium, S] — `surface.md`
8. DX-001 — Quick Start omits `just certs` — fresh clone breaks `docker compose up` [medium, S] — `surface.md`
9. PERF-001 — Health checks run sequentially, blocking the whole sweep on slow/dead workers [medium, S] — `operations.md`
10. SEC-001 — Dev cert private keys written world-readable (mode 0644) [medium, S] — `operations.md`
11. SEC-002 — Worker registration fails open when ACHERON_REGISTRATION_TOKEN is unset [medium, S] — `operations.md`
12. SEC-003 — TLS silently disabled when CA env vars are unset (gRPC insecure_channel / uvicorn plain HTTP) [medium, S] — `operations.md`
13. TEST-001 — local_handlers.py has zero direct unit tests [medium, S] — `verification.md`
14. TEST-004 — Conftest make_app and other API test sites do not inject job_store, leaking env-config dependence into the test suite [medium, S] — `verification.md`
15. TYPE-002 — PlanResult.status and TrackedJob.status are stringly-typed with a vocabulary diverging from the JobStatus enum [medium, S] — `code-quality.md`

## Story counts

| Status | Count |
|---|---|
| open | 38 |
| in-progress | 0 |
| fixed | 0 |
| verified | 10 |
| stale | 1 |
| wontfix | 0 |

## Last orientation snapshot

### Repository at a glance

- **Project**: acheron — distributed async audio-transformation pipeline (EPUB/audio → offline chapterized audiobooks in a target language). Python 3.14+, hatchling, uv, just.
- **Branch**: `chore/code-review-update` · **HEAD**: `a1b11b2`
- **Top-level dirs**:
  - `src/acheron/` — core domain + shell services
  - `tests/` — mirrors `src/`
  - `dashboard/`, `stubs/`, `proto/`, `scripts/`, `docs/`
  - `certs/` (gitignored), `.worktrees/` (gitignored)
- **Notable absences**: NOT hexagonal (no `application/`/`infrastructure/`/`ports.py`); NOT dbt; NO `.agentic-rules/` dir.

### Architecture: core/shell boundary

- `src/acheron/core/` — domain: `chunking.py`, `errors.py`, `interfaces.py` (ABCs: Worker, StreamingWorker, Executor), `models.py`, `planner.py`. core/ MUST NEVER import shell/ — enforced by import-linter contract `core-shell-boundary`.
- `src/acheron/shell/` — services:
  - `api/` — FastAPI routes (capabilities, jobs, workers), schemas, deps, app
  - `executors/` — Sequential, Async, Streaming [default]; `batch_async.py` removed
  - `stores/` — `base.py` (WorkerStore + JobStore ABCs), `memory.py`, `redis.py`
  - `transports/` — HttpWorker, GrpcWorker, LocalWorker
  - `orchestrator.py` (~270 lines after ARCH-003 refactor), `cache.py` (async StepCache), `capabilities.py` (extracted), `registry.py`, `step_handler.py`, `local_handlers.py`, `job_store.py`, `health.py`, `tls.py`
- `src/acheron/` top-level: `cli.py` (click), `api_client.py`
- ABCs throughout. Storage: `ACHERON_STORE_BACKEND=memory|redis`.

### Test landscape

- `tests/core/`, `tests/shell/`, `tests/shell/api/`, `tests/shell/stores/`, `tests/integration/`. pytest: `--cov=acheron --cov-fail-under=80`, xdist, `xfail_strict`, asyncio.

### Tooling

- Justfile: `lint-strict`, `type-check`, `type-check-pyright`, `test`, `lint-imports`, `proto`, `validate`, `install`, `certs`.
- pyproject.toml: ruff `select=ALL` + google pydocstyle; mypy strict; basedpyright standard; import-linter `core-shell-boundary`.

### Key entry points

- CLI: `acheron submit/status/jobs/workers/capabilities` (src/acheron/cli.py)
- API: FastAPI app at `src/acheron/shell/api/app.py` (now wires both stores per ARCH-002)
- Orchestrator: `src/acheron/shell/orchestrator.py` (delegates capability aggregation to `capabilities.py` per ARCH-003)

### Changes since last review (23c29e1..a1b11b2)

11 commits, +1924/-610 across 45 files. Notable: `executors/batch_async.py` deleted, `capabilities.py` extracted, `api/app.py` wires both stores, `orchestrator.py` 345→~270 lines, `api_client.py` adds `_ssl_context_for` helper, metadata unified to `dict[str, JsonValue]` across registry/stores/schema.
