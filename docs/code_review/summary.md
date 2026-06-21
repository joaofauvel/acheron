---
branch: chore/code-review-update
initial_review_commit: 23c29e1
last_updated_commit: d9dc740
last_staleness_scan:
  commit: be7b3ab
  date: 2026-06-20
---

# Code Review Summary

## Per-theme grades

| Theme | Grade | Stories (open/in-progress/stale) |
|---|---|---|
| CORR | A | 0 critical, 0 high, 1 medium, 0 low |
| ML | A | 0 critical, 0 high, 0 medium, 0 low |
| MATH | A | 0 critical, 0 high, 0 medium, 0 low |
| ARCH | A | 0 critical, 0 high, 0 medium, 1 low |
| CFG | A | 0 critical, 0 high, 0 medium, 0 low |
| MAINT | A | 0 critical, 0 high, 1 medium, 1 low |
| EXC | A | 0 critical, 0 high, 1 medium, 0 low |
| TYPE | A | 0 critical, 0 high, 1 medium, 0 low |
| TEST | A | 0 critical, 0 high, 1 medium, 0 low |
| REPRO | A | 0 critical, 0 high, 1 medium, 0 low |
| DATA | A | 0 critical, 0 high, 0 medium, 0 low |
| PERF | A | 0 critical, 0 high, 0 medium, 0 low |
| OBS | A | 0 critical, 0 high, 1 medium, 1 low |
| SEC | B | 0 critical, 1 high, 0 medium, 2 low |
| DX | A | 0 critical, 0 high, 0 medium, 0 low |
| PKG | A | 0 critical, 0 high, 0 medium, 0 low |
| DOC | A | 0 critical, 0 high, 0 medium, 0 low |

## Top concerns

1. SEC-007 — Host Path Traversal & Arbitrary Local File Read in ExtractionHandler [high] — `operations.md`
2. CORR-009 — Step handler caches worker list and worker instances across steps and plans [medium] — `correctness.md`
3. MAINT-002 — redis.py hand-rolls JSON ser/deser duplicating pydantic path [medium] — `code-quality.md`
4. EXC-001 — tenacity unused; transient network calls have no retry [medium] — `code-quality.md`
5. TYPE-001 — AcheronClient returns dict[str, Any] consumed via magic-string keys [medium] — `code-quality.md`
6. TEST-002 — misleading test name claims Redis coverage while testing memory [medium] — `verification.md`
7. REPRO-001 — Redis list_all() non-deterministic order [medium] — `verification.md`
8. OBS-001 — Shutdown does not drain in-flight _execute tasks [medium] — `operations.md`
9. ARCH-008 — Orchestrator.__init__ still derives default StepCache from PlanCache.data_dir [low] — `architecture.md`
10. MAINT-005 — Orchestrator._execute duplicates PlanResult construction across adjacent exception handlers [low] — `code-quality.md`

## Quick wins

1. CORR-009 — Step handler caches worker list and worker instances across steps and plans [medium, S effort] — `correctness.md`

## Story counts

| Status | Count |
|---|---|
| open | 13 |
| in-progress | 0 |
| fixed | 0 |
| verified | 43 |
| stale | 0 |
| wontfix | 0 |

## Changes since last review

The diff d0b739b..be7b3ab resolved 17 stories that were `fixed` with pending placeholders — now graduated to `verified` after the refresh confirmed the fixes are sound and no open stories were invalidated. Four new stories surfaced from the changes:

- **CORR-009**: step handler caches `registry.list_all()` per plan and reuses `Worker` instances per worker_id, creating a stale-dispatch risk if registrations change.
- **ARCH-008**: residual coupling — `Orchestrator.__init__` still derives the default `StepCache` from `PlanCache.data_dir`.
- **MAINT-005**: `Orchestrator._execute` duplicates the same `PlanResult` constructor in its adjacent exception handlers.
- **SEC-006**: the OBS-004 fix persists raw `str(exc)` in API responses, potentially exposing internal paths or endpoints.
- **SEC-007**: host path traversal and arbitrary local file read vulnerability in `ExtractionHandler` via un-sandboxed `source_path`.

16 themes still grade A (SEC grades B).

## Last orientation snapshot

**Repository**: acheron — audiobook processing pipeline (FastAPI orchestrator + gRPC/HTTP workers + Redis/memory stores).

**Branch**: chore/code-review-update, HEAD: be7b3ab

**Top-level layout**: `src/acheron/core/` (domain models, errors, chunking, planner, interfaces), `src/acheron/shell/` (orchestrator, API, executors: streaming/async/sequential, stores: memory/redis, transports: http/grpc/local, cache, health, TLS, step_handler, local_handlers, capabilities), `dashboard/` (separate package), `stubs/` (dev workers), `tests/` (mirrors src/).

**No hexagonal layers**: flat package structure. Interfaces (ABCs) in `core/interfaces.py`. No `ports.py` files.

**Test landscape**: tests/core/, tests/shell/ (api/, stores/), tests/integration/, tests/scripts/. New since last review: step_handler tests for list_all caching and worker-instance reuse, health-monitor polling helper, data-dir tests updated for async `start()`.

**Tooling**: `just certs install lint-imports lint-strict proto test type-check type-check-pyright validate`. All deps `~=` pinned. jinja2 in optional-dependencies[dashboard].

**Key entry points**: `acheron.cli:main`, `acheron.shell.api.__main__`, `acheron.shell.api.app:create_app`.
