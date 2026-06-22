---
branch: chore/code-review-update
initial_review_commit: 23c29e1
last_updated_commit: 63faed4
last_staleness_scan:
  commit: 63faed4
  date: 2026-06-21
---

# Code Review Summary

## Per-theme grades

| Theme | Grade | Stories (open/in-progress/stale) |
|---|---|---|
| CORR | A | 0 critical, 0 high, 2 medium, 2 low |
| ML | A | 0 critical, 0 high, 0 medium, 0 low |
| MATH | A | 0 critical, 0 high, 0 medium, 0 low |
| ARCH | A | 0 critical, 0 high, 1 medium, 2 low |
| CFG | B | 0 critical, 0 high, 3 medium, 0 low |
| MAINT | B | 0 critical, 0 high, 3 medium, 2 low |
| EXC | A | 0 critical, 0 high, 1 medium, 1 low |
| TYPE | A | 0 critical, 0 high, 2 medium, 1 low |
| TEST | A | 0 critical, 0 high, 2 medium, 2 low |
| REPRO | A | 0 critical, 0 high, 1 medium, 0 low |
| DATA | A | 0 critical, 0 high, 1 medium, 0 low |
| PERF | A | 0 critical, 0 high, 2 medium, 0 low |
| OBS | A | 0 critical, 0 high, 2 medium, 1 low |
| SEC | C | 1 critical, 2 high, 0 medium, 3 low |
| DX | A | 0 critical, 0 high, 1 medium, 0 low |
| PKG | A | 0 critical, 0 high, 0 medium, 0 low |
| DOC | A | 0 critical, 0 high, 1 medium, 0 low |

## Top concerns

1. SEC-008 — Auto-generated registration token is logged in plaintext at startup [critical] — `operations.md`
2. SEC-007 — Host Path Traversal & Arbitrary Local File Read in ExtractionHandler [high] — `operations.md`
3. SEC-009 — Registration token file created with process umask (potentially world-readable) [high] — `operations.md`
4. ARCH-009 — HealthProvider ABC lives in shell/health_providers.py instead of core/interfaces.py [medium] — `architecture.md`
5. CFG-003 — `ACHERON_OPEN_REGISTRATION` read directly in deps.py, bypassing the new settings loader [medium] — `architecture.md`
6. CFG-004 — Orchestrator mutates `Settings.orchestrator.data_dir` in-place from two call sites [medium] — `architecture.md`
7. CFG-005 — `${VAR}` env-var expansion silently substitutes unset env vars as empty strings, disabling providers [medium] — `architecture.md`
8. CORR-009 — Step handler caches worker list and worker instances across steps and plans [medium] — `correctness.md`
9. CORR-010 — `${VAR}` env-var expansion silently substitutes missing variables with empty string [medium] — `correctness.md`
10. DATA-005 — RedisWorkerStore._deserialize_worker invalid status field has no corruption test [medium] — `verification.md`

## Quick wins

1. SEC-008 — Auto-generated registration token is logged in plaintext at startup [critical, S effort] — `operations.md`
2. SEC-009 — Registration token file created with process umask (potentially world-readable) [high, S effort] — `operations.md`
3. ARCH-009 — HealthProvider ABC lives in shell/health_providers.py instead of core/interfaces.py [medium, S effort] — `architecture.md`
4. CFG-003 — `ACHERON_OPEN_REGISTRATION` read directly in deps.py, bypassing the new settings loader [medium, S effort] — `architecture.md`
5. CFG-004 — Orchestrator mutates `Settings.orchestrator.data_dir` in-place from two call sites [medium, S effort] — `architecture.md`
6. CFG-005 — `${VAR}` env-var expansion silently substitutes unset env vars as empty strings [medium, S effort] — `architecture.md`
7. CORR-009 — Step handler caches worker list and worker instances across steps and plans [medium, S effort] — `correctness.md`
8. CORR-010 — `${VAR}` env-var expansion silently substitutes missing variables with empty string [medium, S effort] — `correctness.md`
9. MAINT-006 — Orchestrator.start() inlines 17-line registration-token block; logs the token in plaintext [medium, S effort] — `code-quality.md`
10. DATA-005 — RedisWorkerStore._deserialize_worker invalid status field has no corruption test [medium, S effort] — `verification.md`

## Story counts

| Status | Count |
|---|---|
| open | 39 |
| in-progress | 0 |
| fixed | 0 |
| verified | 43 |
| stale | 0 |
| wontfix | 0 |

## Changes since last review

The diff `d9dc740..63faed4` covers the close-out of Layer 10 (configuration files, registration-token auto-generation) and the full Layer 11 health-checks-and-dashboard slice (HealthProvider ABC + RunPod/HuggingFace providers, HealthMonitor with `WorkerStatus`/`last_error`, store status persistence, dashboard status badges + error viewer, `/partials/status` endpoint, `/workers` exposing `status`/`last_error`). No previously verified stories regressed; 11 open carry-overs (CORR-009, ARCH-008, MAINT-002, EXC-001, TYPE-001, MAINT-005, TEST-002, REPRO-001, OBS-001, OBS-003, SEC-005, SEC-006, SEC-007) were re-resolved against the new line numbers. 26 new findings surfaced, dominated by:

- **SEC-008 (critical)**: auto-generated registration token is logged in plaintext at startup — partially undoes the SEC-002 mitigation.
- **SEC-009 (high)**: registration token file is written without `chmod 0o600` (the same anti-pattern SEC-001 flagged for CA private keys).
- **3 CFG findings**: `ACHERON_OPEN_REGISTRATION` read outside the new settings loader, in-place mutation of `Settings.orchestrator.data_dir` from two call sites, and `${VAR}` env-var expansion silently substituting unset env vars as empty strings.
- **3 CORR findings**: the env-var expansion silent-empty and uppercase-only pattern, plus a missing duration bound on the BOOTING status from health providers.
- **2 ARCH findings**: HealthProvider ABC placement (drift from "interfaces in `core/interfaces.py`") and a no-behavior `HealthProviders` wrapper class.
- **2 PERF findings**: the new health-monitor post-probe bookkeeping and provider status checks are sequential and can starve the 30s interval under load.
- **OBS-005**: health providers swallow `(httpx.HTTPError, OSError)` silently with no diagnostic log, masking configuration mistakes.
- **Multiple MAINT/EXC/TYPE findings** for the new code (token block inlined, fetch envelope duplicated, error parameter reassigned, broad `except Exception`, redis.py `# type: ignore` markers, `WorkerResponse.status` stringly-typed).
- **Multiple TEST/DATA findings** for missing direct tests of new helpers and symmetric corruption tests.
- **2 Surface findings**: README Quick Start uses `acheron submit` (real subcommand is `submit-job`); config docs drift across `.env.example`, README, and an undocumented `ACHERON_TRUST_REVERSE_PROXY` dashboard env var.

Three pending SHA placeholders (MAINT-002, EXC-001, REPRO-001) could not be resolved — `git log --grep='(<ID>)' --oneline` returned no commits in this delta. They remain `pending` per update-mode rules. No stories were marked `stale` (the cited code still exists at re-resolved line numbers).

Grades: 14 themes at A, 2 at B (CFG, MAINT), 1 at C (SEC — driven by SEC-008 critical). No aggregate codebase grade (per the rubric).

## Last orientation snapshot

**Repository**: acheron — audiobook processing pipeline (FastAPI orchestrator + gRPC/HTTP workers + Redis/memory stores). Greenfield (per AGENTS.md).

**Branch / HEAD**: `chore/code-review-update` at `63faed4` (synced with master for this refresh).

**Top-level layout**: `src/acheron/core/` (domain models, errors, chunking, planner, interfaces), `src/acheron/shell/` (orchestrator, API, executors: streaming/async/sequential, stores: memory/redis, transports: http/grpc/local, cache, health, TLS, step_handler, local_handlers, capabilities, health_providers, config), `dashboard/` (separate package), `stubs/` (dev workers), `tests/` (mirrors src/).

**No hexagonal layers**: flat package structure. Interfaces (ABCs) in `core/interfaces.py`. No `ports.py` files.

**Test landscape**: tests/core/, tests/shell/ (api/, stores/), tests/integration/, tests/scripts/. New since last review: full health_providers test module, partials API tests, expanded orchestrator/config/health_monitor tests.

**Tooling**: `just certs install lint-imports lint-strict proto test type-check type-check-pyright validate`. All deps `~=` pinned. jinja2 in optional-dependencies[dashboard].

**Key entry points**: `acheron.cli:main`, `acheron.shell.api.__main__`, `acheron.shell.api.app:create_app`.
