---
branch: chore/code-review-update
initial_review_commit: 23c29e1
last_updated_commit: d0b739b
last_staleness_scan:
  commit: d0b739b
  date: 2026-06-20
---

# Code Review Summary

## Per-theme grades

| Theme | Grade | Stories (open/in-progress/stale) |
|---|---|---|
| CORR | A | 0 critical, 0 high, 0 medium, 2 low |
| ML | A | 0 critical, 0 high, 0 medium, 0 low |
| MATH | A | 0 critical, 0 high, 0 medium, 0 low |
| ARCH | A | 0 critical, 0 high, 0 medium, 3 low |
| CFG | A | 0 critical, 0 high, 0 medium, 1 low |
| MAINT | A | 0 critical, 0 high, 1 medium, 2 low |
| EXC | A | 0 critical, 0 high, 1 medium, 1 low |
| TYPE | A | 0 critical, 0 high, 1 medium, 0 low |
| TEST | A | 0 critical, 0 high, 1 medium, 1 low |
| REPRO | A | 0 critical, 0 high, 1 medium, 1 low |
| DATA | A | 0 critical, 0 high, 0 medium, 0 low |
| PERF | A | 0 critical, 0 high, 2 medium, 0 low |
| OBS | A | 0 critical, 0 high, 1 medium, 3 low |
| SEC | A | 0 critical, 0 high, 0 medium, 2 low |
| DX | A | 0 critical, 0 high, 0 medium, 0 low |
| PKG | A | 0 critical, 0 high, 0 medium, 0 low |
| DOC | A | 0 critical, 0 high, 0 medium, 1 low |

## Top concerns

1. MAINT-002 — redis.py hand-rolls JSON ser/deser duplicating pydantic path [medium] — `code-quality.md`
2. EXC-001 — tenacity unused; transient network calls have no retry [medium] — `code-quality.md`
3. TYPE-001 — AcheronClient returns dict[str, Any] consumed via magic-string keys [medium] — `code-quality.md`
4. TEST-002 — misleading test name claims Redis coverage while testing memory [medium] — `verification.md`
5. REPRO-001 — Redis list_all() non-deterministic order [medium] — `verification.md`
6. PERF-002 — Registry list_all() called per step in dispatch hot path [medium] — `operations.md`
7. PERF-003 — Worker transport instances reconstructed per step no connection reuse [medium] — `operations.md`
8. OBS-001 — Shutdown does not drain in-flight _execute tasks [medium] — `operations.md`

## Quick wins

No quick wins this cycle — all medium+ severity open stories require effort M. The previous wave of S-effort stories (SEC-001/2/3, DATA-001/2/4, TEST-001, etc.) is now fixed.

## Story counts

| Status | Count |
|---|---|
| open | 25 |
| in-progress | 0 |
| fixed | 16 |
| verified | 10 |
| stale | 0 |
| wontfix | 0 |

## Changes since last review

The diff a1b11b2..d0b739b resolved 15 pending stories — a concentrated fix wave addressing the B-grade themes from the initial review:

- **TEST improved B→A**: TEST-001 (local_handlers unit tests) and TEST-004 (conftest job_store injection) fixed. 1 medium + 1 low remain.
- **DATA improved B→A**: DATA-001 (extra='forbid'), DATA-002 (CacheCorruptedError wrapping), DATA-003 (metadata round-trip tests), DATA-004 (worker metadata coverage) all fixed. Zero open.
- **PERF improved B→A**: PERF-001 (concurrent health probes) fixed. 2 medium remain (list_all N+1, transport reuse).
- **SEC improved B→A**: SEC-001 (private key 0600), SEC-002 (registration opt-in), SEC-003 (TLS warnings) fixed. 2 low remain (X-Forwarded-User, unauthenticated routes).

New stories: ARCH-007 (`_stage` parameter bloat + mutable-list side-channel, low) and MAINT-004 (`_stage` dead return value, low).

All 17 themes now grade A.

## Last orientation snapshot

**Repository**: acheron — audiobook processing pipeline (FastAPI orchestrator + gRPC/HTTP workers + Redis/memory stores).

**Branch**: chore/code-review-update, HEAD: d0b739b

**Top-level layout**: `src/acheron/core/` (domain models, errors, chunking, planner, interfaces), `src/acheron/shell/` (orchestrator, API, executors: streaming/async/sequential, stores: memory/redis, transports: http/grpc/local, cache, health, TLS, step_handler, local_handlers, capabilities), `dashboard/` (separate package), `stubs/` (dev workers), `tests/` (mirrors src/).

**No hexagonal layers**: flat package structure. Interfaces (ABCs) in `core/interfaces.py`. No `ports.py` files.

**Test landscape**: tests/core/, tests/shell/ (api/, stores/), tests/integration/, tests/scripts/. New since last review: tests/shell/test_local_handlers.py, tests/shell/stores/test_init.py.

**Tooling**: `just certs install lint-imports lint-strict proto test type-check type-check-pyright validate`. All deps `~=` pinned. jinja2 moved to optional-dependencies[dashboard].

**Key entry points**: `acheron.cli:main`, `acheron.shell.api.__main__`, `acheron.shell.api.app:create_app`.
