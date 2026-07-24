---
branch: master
initial_review_commit: 23c29e1
last_updated_commit: a749f8f
last_staleness_scan:
  commit: a749f8f
  date: 2026-07-23
---

# Code Review Summary

## Per-theme grades

| Theme | Grade | Stories (open/in-progress/stale by severity) |
|---|---|---|
| ARCH | A | 0 critical, 0 high, 2 medium, 0 low |
| CFG | A | 0 critical, 0 high, 0 medium, 0 low |
| CORR | A | 0 critical, 0 high, 2 medium, 0 low |
| DATA | A | 0 critical, 0 high, 0 medium, 0 low |
| DOC | A | 0 critical, 0 high, 1 medium, 0 low |
| DX | A | 0 critical, 0 high, 0 medium, 0 low |
| EXC | A | 0 critical, 0 high, 0 medium, 0 low |
| MAINT | A | 0 critical, 0 high, 0 medium, 1 low |
| OBS | A | 0 critical, 0 high, 0 medium, 0 low |
| PERF | A | 0 critical, 0 high, 0 medium, 1 low |
| PKG | A | 0 critical, 0 high, 0 medium, 0 low |
| REPRO | A | 0 critical, 0 high, 0 medium, 0 low |
| SEC | A | 0 critical, 0 high, 0 medium, 0 low |
| TEST | A | 0 critical, 0 high, 1 medium, 0 low |
| TYPE | A | 0 critical, 0 high, 0 medium, 0 low |

Themes dropped from the rubric since the 8c baseline remain empty: **ML** and **MATH**.

## Top Concerns

No high-severity open or stale stories were found. Remaining stale concerns:

1. ARCH-011 — `worker_sdk/__init__.py` docstring falsely claims the module is GPU-SDK-free at import time [medium, S] — `architecture.md`
2. ARCH-012 — `create_worker_app` cherry-picks routes via a hardcoded `inner_paths` set [medium, S] — `architecture.md`
3. CORR-015 — `create_worker_app` silently drops routes added outside `inner_paths` [medium, S] — `correctness.md`
4. CORR-034 — Python 2 exception syntax was re-introduced across five sites [medium, S] — `correctness.md`
5. DOC-004 — README omits the `granite_speech` worker from architecture, CI, and test paths [medium, S] — `surface.md`
6. TEST-014 — TranslateGemma tests miss model-generation failure and partial-success paths [medium, M] — `verification.md`
7. MAINT-020 — exception-syntax cleanup regressed at multiple sites [low, S] — `code-quality.md`
8. PERF-008 — `HttpWorker._post_multipart` creates an `AsyncClient` per call [low, S] — `operations.md`

## Quick Wins

1. ARCH-011 — correct the worker SDK package documentation [medium, S] — `architecture.md`
2. ARCH-012 — remove hardcoded route selection [medium, S] — `architecture.md`
3. CORR-015 — make edge route registration complete [medium, S] — `correctness.md`
4. CORR-034 — restore parenthesized exception syntax [medium, S] — `correctness.md`
5. DOC-004 — document the `granite_speech` worker consistently [medium, S] — `surface.md`

## Story Counts

| Status | Count |
|---|---|
| open | 0 |
| in-progress | 0 |
| fixed | 61 |
| verified | 175 |
| stale | 8 |
| wontfix | 0 |

## Changes Since Last Review

The review scan covers `c53da1d..e0246e0`: 35 modified files, with 1,133 insertions and 272 deletions. Round 6 then verified all 11 resulting stories across 11 atomic commits through `a749f8f`; the ledger consolidation was committed as `9220781`.

Round 6 addressed shutdown persistence bounds, Redis async-surface validation, BOOTING-state lifecycle, worker retirement cleanup, cancellation-path tests, and worker CI path coverage. Commit `de347c2` subsequently fixed the RunPod image Python/Torch compatibility issue. The dbt parse command remains unavailable because `resolver` is not installed.

## Last Orientation Snapshot

**Repository**: `acheron`, a FastAPI orchestrator for asynchronous audio transformation with HTTP/gRPC workers and Redis or in-memory stores.

**Branch / HEAD**: `master` at `de347c2`. The review ledger was last scanned through `a749f8f`.

**Top-level layout**: `src/acheron/core/` contains domain models and interfaces; `src/acheron/shell/` contains orchestration, API, stores, transports, health, cache, and configuration; `src/acheron/worker_sdk/` contains worker edge/runtime code; `dashboard/`, `stubs/`, `workers/`, `tests/`, `scripts/`, `proto/`, and `.github/` provide supporting packages and tooling.

**Boundaries**: No `application/`, `infrastructure/`, `models/`, `macros/`, or dbt layer exists. There are no `ports.py` files. Import-linter forbids `acheron.core -> acheron.shell`, `acheron.worker_sdk -> acheron.shell`, and `workers -> acheron.shell`.

**Tests**: `tests/core/`, `tests/shell/`, `tests/worker_sdk/`, `tests/integration/`, and `tests/scripts/` mirror the source packages. Worker packages have their own test directories. The full suite passes with 999 tests and 93.66% coverage.

**Tooling**: `just lint-strict`, `just lint-imports`, `just type-check`, `just type-check-pyright`, `just test`, and `just validate` are the primary quality gates. `just install` installs the uv workspace, and `just build-worker` / `just build-edge` build images.

**Entry points**: `acheron.cli:main`, `acheron.worker_sdk.cli:main`, `acheron.shell.api.app:create_app`, worker RunPod entrypoints, and the worker SDK edge server.
