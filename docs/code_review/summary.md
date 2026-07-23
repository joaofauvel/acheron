---
branch: code-review-refresh
initial_review_commit: 23c29e1
last_updated_commit: e0246e0019c0f3a6596c8ddef3dcf5af3405f5b8
last_staleness_scan:
  commit: e0246e0
  date: 2026-07-23
---

# Code Review Summary

## Per-theme grades

| Theme | Grade | Stories (open/in-progress/stale by severity) |
|---|---|---|
| ARCH | B | 0 critical, 0 high, 3 medium, 1 low |
| CFG | A | 0 critical, 0 high, 0 medium, 1 low |
| CORR | B | 0 critical, 0 high, 7 medium, 4 low |
| DATA | A | 0 critical, 0 high, 0 medium, 2 low |
| DOC | A | 0 critical, 0 high, 1 medium, 1 low |
| DX | A | 0 critical, 0 high, 1 medium, 0 low |
| EXC | A | 0 critical, 0 high, 0 medium, 0 low |
| MAINT | A | 0 critical, 0 high, 1 medium, 1 low |
| OBS | A | 0 critical, 0 high, 2 medium, 1 low |
| PERF | B | 0 critical, 0 high, 5 medium, 1 low |
| PKG | A | 0 critical, 0 high, 0 medium, 0 low |
| REPRO | A | 0 critical, 0 high, 0 medium, 2 low |
| SEC | A | 0 critical, 0 high, 0 medium, 0 low |
| TEST | B | 0 critical, 0 high, 6 medium, 8 low |
| TYPE | A | 0 critical, 0 high, 2 medium, 2 low |

Themes dropped from the rubric since the 8c baseline remain empty: **ML** and **MATH**.

## Top Concerns

No high-severity open stories were found. New medium findings from this refresh:

1. CORR-042 — `Orchestrator.close()` can wait indefinitely for background persistence [S] — `correctness.md`
2. OBS-015 — shutdown can wait indefinitely for background persistence [S] — `operations.md`
3. PERF-010 — worker retirement cleanup scans every active job on every release [M] — `operations.md`
4. PERF-011 — health monitor retains BOOTING timestamps for removed workers [S] — `operations.md`
5. TEST-028 — Redis protocol tests accept synchronous commands [S] — `verification.md`
6. TEST-029 — BOOTING timeout bookkeeping lacks re-registration coverage [S] — `verification.md`
7. TEST-030 — Redis StoreError cancellation contract lacks failure-path coverage [S] — `verification.md`
8. TYPE-014 — Redis Protocol validation checks callable presence but not awaitability [S] — `code-quality.md`
9. DX-007 — worker CI filter omits the root package initializer [S] — `surface.md`
10. CORR-044 — BOOTING timeout state survives worker re-registration [S] — `correctness.md`

## Quick Wins

1. CORR-042 — bound the final background-persistence wait [medium, S] — `correctness.md`
2. OBS-015 — bound and log final reconciliation waits [medium, S] — `operations.md`
3. PERF-011 — clear BOOTING state on worker removal [medium, S] — `operations.md`
4. TEST-028 — reject synchronous Redis protocol members [medium, S] — `verification.md`
5. TEST-029 — cover worker re-registration after BOOTING timeout [medium, S] — `verification.md`
6. TEST-030 — cover StoreError cancellation behavior [medium, S] — `verification.md`
7. TYPE-014 — enforce Redis awaitability at validation time [medium, S] — `code-quality.md`
8. DX-007 — include `src/acheron/__init__.py` in worker CI paths [medium, S] — `surface.md`

## Story Counts

| Status | Count |
|---|---|
| open | 11 |
| in-progress | 0 |
| fixed | 61 |
| verified | 164 |
| stale | 8 |
| wontfix | 0 |

## Changes Since Last Review

The scan covers `c53da1d..e0246e0`: 35 modified files, with 1,133 insertions and 272 deletions. Changes span `.env.example`, `.github/`, `README.md`, `docker-compose.yml`, `docs/code_review/`, `src/acheron/`, `tests/`, and worker READMEs/tests. No files were added or removed.

The new stories focus on shutdown persistence bounds, Redis async-surface validation, BOOTING-state lifecycle, worker retirement cleanup, cancellation-path tests, and worker CI path coverage. Existing fixed/verified/wontfix stories were preserved. Stale citations were re-resolved for the changed route, README, package, and worker SDK files.

## Last Orientation Snapshot

**Repository**: `acheron`, a FastAPI orchestrator for asynchronous audio transformation with HTTP/gRPC workers and Redis or in-memory stores.

**Branch / HEAD**: `code-review-refresh` at `e0246e0`.

**Top-level layout**: `src/acheron/core/` contains domain models and interfaces; `src/acheron/shell/` contains orchestration, API, stores, transports, health, cache, and configuration; `src/acheron/worker_sdk/` contains worker edge/runtime code; `dashboard/`, `stubs/`, `workers/`, `tests/`, `scripts/`, `proto/`, and `.github/` provide supporting packages and tooling.

**Boundaries**: No `application/`, `infrastructure/`, `models/`, `macros/`, or dbt layer exists. There are no `ports.py` files. Import-linter forbids `acheron.core -> acheron.shell`, `acheron.worker_sdk -> acheron.shell`, and `workers -> acheron.shell`.

**Tests**: `tests/core/`, `tests/shell/`, `tests/worker_sdk/`, `tests/integration/`, and `tests/scripts/` mirror the source packages. Worker packages have their own test directories. The full suite previously passed with 988 tests and 93.71% coverage before this documentation-only refresh.

**Tooling**: `just lint-strict`, `just lint-imports`, `just type-check`, `just type-check-pyright`, `just test`, and `just validate` are the primary quality gates. `just install` installs the uv workspace, and `just build-worker` / `just build-edge` build images.

**Entry points**: `acheron.cli:main`, `acheron.worker_sdk.cli:main`, `acheron.shell.api.app:create_app`, worker RunPod entrypoints, and the worker SDK edge server.
