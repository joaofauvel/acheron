---
branch: docs/code-review-initial
initial_review_commit: 23c29e1
last_updated_commit: 23c29e1
last_staleness_scan:
  commit: 23c29e1
  date: 2026-06-19
---

# Code Review Summary

## Per-theme grades

| Theme | Grade | Stories (open/in-progress/stale) |
|---|---|---|
| CORR | C | 1 critical, 0 high, 4 medium, 2 low |
| ML   | A | 0 critical, 0 high, 0 medium, 0 low |
| MATH | A | 0 critical, 0 high, 0 medium, 0 low |
| ARCH | B | 0 critical, 1 high, 3 medium, 0 low |
| CFG  | A | 0 critical, 0 high, 1 medium, 1 low |
| MAINT | A | 0 critical, 1 high, 1 medium, 0 low |
| EXC  | A | 0 critical, 0 high, 1 medium, 1 low |
| TYPE | A | 0 critical, 0 high, 2 medium, 0 low |
| TEST | A | 0 critical, 0 high, 2 medium, 1 low |
| REPRO | A | 0 critical, 0 high, 1 medium, 1 low |
| DATA | B | 0 critical, 0 high, 3 medium, 0 low |
| PERF | B | 0 critical, 0 high, 3 medium, 0 low |
| OBS  | A | 0 critical, 0 high, 1 medium, 3 low |
| SEC  | B | 0 critical, 0 high, 3 medium, 2 low |
| DX   | A | 0 critical, 0 high, 1 medium, 0 low |
| PKG  | A | 0 critical, 0 high, 0 medium, 1 low |
| DOC  | A | 0 critical, 0 high, 0 medium, 1 low |

## Top concerns

Stories sorted by severity desc, then ID asc, top 10 (or all if fewer than 10). Each entry cites the bundle file the story lives in.

1. CORR-001 — StreamingExecutor ignores JobResult.status — FAILED results silently treated as SUCCESS [critical] — `correctness.md`
2. ARCH-001 — BatchAsyncExecutor is a no-op duplicate of AsyncExecutor; ExecutorStrategy.BATCH_ASYNC controls nothing [high] — `architecture.md`
3. MAINT-001 — BatchAsyncExecutor is a verbatim duplicate of AsyncExecutor; entire batch submission machinery is vestigial [high] — `code-quality.md`
4. CORR-002 — BatchAsyncExecutor duplicates AsyncExecutor — batch flag never checked, no batch submission implemented [medium] — `correctness.md`
5. CORR-003 — GrpcWorker.submit_batch — all-or-nothing gather, synchronous execution, state lost across factory instances [medium] — `correctness.md`
6. CORR-004 — SequentialExecutor lets handler exceptions propagate — no PlanResult returned, API shows total_steps=0 [medium] — `correctness.md`
7. CORR-005 — ASR worker selection ignores output language — may dispatch worker that can't produce required output [medium] — `correctness.md`
8. ARCH-002 — Store construction asymmetry: app.py injects WorkerStore but lets Orchestrator create JobStore internally [medium] — `architecture.md`
9. ARCH-003 — Orchestrator accretes capability aggregation, worker registration, job lifecycle, and data-dir verification in one 345-line class [medium] — `architecture.md`
10. ARCH-004 — metadata typed dict[str, object] on RegisteredWorker/WorkerStore ABC vs dict[str, JsonValue] on WorkerCapabilities [medium] — `architecture.md`

## Quick wins

Stories with `effort: S` AND `severity ∈ {medium, high, critical}` AND `status ∈ {open, in-progress, stale}`:

1. CORR-004 — SequentialExecutor lets handler exceptions propagate — no PlanResult returned [medium, S] — `correctness.md`
2. CORR-005 — ASR worker selection ignores output language [medium, S] — `correctness.md`
3. CFG-001 — ACHERON_STORE_BACKEND / REDIS_URL selection logic duplicated [medium, S] — `architecture.md`
4. ARCH-002 — Store construction asymmetry [medium, S] — `architecture.md`
5. ARCH-004 — metadata typed dict[str, object] vs dict[str, JsonValue] [medium, S] — `architecture.md`
6. TYPE-002 — PlanResult.status and TrackedJob.status are stringly-typed [medium, S] — `code-quality.md`
7. TEST-001 — local_handlers.py has zero direct unit tests [medium, S] — `verification.md`
8. DATA-001 — API pydantic schemas accept arbitrary extra fields [medium, S] — `verification.md`
9. DATA-002 — Redis deserialization corruption handling inconsistent [medium, S] — `verification.md`
10. DATA-003 — Redis store round-trip gaps: PlanStep.batch=True and non-empty metadata untested [medium, S] — `verification.md`
11. PERF-001 — Health checks run sequentially, blocking the whole sweep on slow/dead workers [medium, S] — `operations.md`
12. SEC-001 — Dev cert private keys written world-readable (mode 0644) [medium, S] — `operations.md`
13. SEC-002 — Worker registration fails open when ACHERON_REGISTRATION_TOKEN is unset [medium, S] — `operations.md`
14. SEC-003 — TLS silently disabled when CA env vars are unset [medium, S] — `operations.md`
15. DX-001 — Quick Start omits `just certs` — fresh clone breaks `docker compose up` [medium, S] — `surface.md`

## Story counts

| Status | Count |
|---|---|
| open | 42 |
| in-progress | 0 |
| fixed | 0 |
| verified | 0 |
| stale | 0 |
| wontfix | 0 |

## Last orientation snapshot

### Repository at a glance

- **Project**: acheron — distributed async audio-transformation pipeline (EPUB/audio → offline chapterized audiobooks in a target language). Python 3.14+, hatchling, uv, just.
- **Branch**: `docs/code-review-initial` · **HEAD**: `23c29e1`
- **Top-level dirs**:
  - `src/acheron/` — core domain + shell services (3966 lines / 44 Python files)
  - `tests/` — 48 test files mirroring src/
  - `dashboard/` — HTMX monitoring dashboard (FastAPI + Jinja2, separate package)
  - `stubs/` — dev stub workers (HTTP + gRPC + translation) + tests
  - `proto/` — `synthesis.proto` (generated code in `src/acheron/proto/`, gitignored)
  - `scripts/` — `generate_dev_certs.py`
  - `certs/` — gitignored dev TLS certs
  - `docs/` — specs/plans/reviews
- **Notable absences**: NO dbt (no `models/`, no `macros/`). NOT hexagonal (no `application/`/`infrastructure/`/`ports.py`). NO `.agentic-rules/` dir. NOT an ML/forecasting codebase.

### Architecture: core/shell boundary

- `src/acheron/core/` — domain: `chunking.py`, `errors.py`, `interfaces.py` (ABCs: Worker, StreamingWorker, Executor), `models.py`, `planner.py`. core/ MUST NEVER import shell/ — enforced by import-linter contract `core-shell-boundary` (pyproject.toml:127-131).
- `src/acheron/shell/` — services:
  - `api/` — FastAPI routes (capabilities, jobs, workers), schemas, deps
  - `executors/` — Sequential, Async, BatchAsync, Streaming [default]; `_utils.py`
  - `stores/` — `base.py` (WorkerStore + JobStore ABCs), `memory.py`, `redis.py` [393 lines, largest]
  - `transports/` — HttpWorker, GrpcWorker, LocalWorker
  - `orchestrator.py` (345 lines), `cache.py` (async StepCache via aiofiles), `registry.py`, `step_handler.py`, `local_handlers.py`, `job_store.py`, `health.py`, `tls.py`
- `src/acheron/` top-level: `cli.py` (255, click), `api_client.py`
- Abstractions use ABCs (not Protocols). Storage backend: `ACHERON_STORE_BACKEND=memory|redis`.
- Size hotspots: stores/redis.py 393, orchestrator.py 345, cli.py 255, executors/streaming.py 232, planner.py 210, models.py 192.

### Test landscape

- `tests/core/` — chunking, errors, interfaces, models, planner (mirrors core/)
- `tests/shell/` — orchestrator, executors, streaming, transports, cache, cli, health, tls, step_handler, data_dir
- `tests/shell/api/` — capabilities, jobs, workers, main
- `tests/shell/stores/` — base, memory (job+worker), redis (job+worker via testcontainers), async, factory
- `tests/integration/` — full lifecycle, multi-job, worker registration/integration, TLS, cli errors (real HTTP/gRPC stubs)
- `tests/scripts/` — generate_dev_certs
- `stubs/tests/` — stub worker entry points, health, TLS, translation
- `dashboard/tests/` — dashboard
- pytest: `--cov=acheron --cov-fail-under=80`, xdist `-n auto`, `xfail_strict`, `--strict-markers`, asyncio. testpaths = `tests`, `stubs/tests`.

### Tooling

- Justfile: `lint-strict` (ruff format+check --fix), `type-check` (mypy strict), `type-check-pyright` (basedpyright), `test` (pytest), `lint-imports` (import-linter), `proto`, `validate` (= lint-strict lint-imports type-check type-check-pyright test), `install`, `certs`.
- pyproject.toml: ruff `select=ALL` + google pydocstyle; mypy strict + `warn_unused_ignores` + `disallow_any_generics`; basedpyright standard; import-linter `core-shell-boundary`; deps pinned `~=` in `[project.dependencies]`; dev tools in `[dependency-groups] dev`; `[project.scripts] acheron = "acheron.cli:main"`; wheel targets `packages = ["src/acheron"]`.

### Key entry points

- CLI: `acheron submit/status/jobs/workers/capabilities` (src/acheron/cli.py)
- API: FastAPI app at `src/acheron/shell/api/app.py` (routes under `api/routes/`)
- Orchestrator: `src/acheron/shell/orchestrator.py` (job lifecycle, worker registration, capabilities)
- Executors: `src/acheron/shell/executors/` (Streaming is the default strategy)
- Docker Compose: redis, orchestrator, dashboard, tts-stub, asr-stub, tts-grpc-stub, translation-stub
