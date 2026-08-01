---
branch: docs/code-review-refresh
initial_review_commit: 23c29e1
last_updated_commit: 22d20f5028d64c8fdac61ad9c7871397c7cf178e
last_staleness_scan:
  commit: 22d20f5028d64c8fdac61ad9c7871397c7cf178e
  date: 2026-08-01
---

# Code Review Summary

## Per-theme grades

| Theme | Grade | Stories (open/in-progress/stale by severity) |
|---|---|---|
| ARCH | B | 0 critical, 1 high, 4 medium, 1 low |
| CFG | A | 0 critical, 0 high, 0 medium, 0 low |
| CORR | B | 0 critical, 0 high, 5 medium, 1 low |
| DATA | A | 0 critical, 0 high, 0 medium, 1 low |
| DOC | A | 0 critical, 0 high, 2 medium, 0 low |
| DX | A | 0 critical, 1 high, 2 medium, 0 low |
| EXC | A | 0 critical, 0 high, 2 medium, 0 low |
| MAINT | A | 0 critical, 0 high, 2 medium, 1 low |
| OBS | A | 0 critical, 0 high, 1 medium, 0 low |
| PERF | B | 0 critical, 0 high, 4 medium, 1 low |
| PKG | A | 0 critical, 0 high, 0 medium, 0 low |
| REPRO | A | 0 critical, 0 high, 0 medium, 1 low |
| SEC | A | 0 critical, 0 high, 0 medium, 4 low |
| TEST | B | 0 critical, 0 high, 4 medium, 0 low |
| TYPE | A | 0 critical, 0 high, 0 medium, 2 low |
| ML | A | 0 critical, 0 high, 0 medium, 0 low |
| MATH | A | 0 critical, 0 high, 0 medium, 0 low |

## Top Concerns

1. **ARCH-027** — Remote workers cannot read upstream manifests from the orchestrator's cache [high, M] — `architecture.md`
2. **DX-008** — Dashboard output links may expose an internal orchestrator hostname [high, M] — `surface.md`
3. **ARCH-011** — `worker_sdk/__init__.py` docstring falsely claims the module is GPU-SDK-free at import time [medium, S] — `architecture.md`
4. **ARCH-012** — `create_worker_app` cherry-picks routes from `EdgeApp.app.routes` via a hardcoded `inner_paths` set [medium, S] — `architecture.md`
5. **ARCH-028** — Plan cache and orchestrator data roots can diverge [medium, S] — `architecture.md`
6. **ARCH-029** — AcheronClient still imports the server-only CleanupResponse schema after response-schema extraction [medium, S] — `architecture.md`
7. **CORR-015** — `create_worker_app` silently drops routes added outside `inner_paths` [medium, S] — `correctness.md`
8. **CORR-034** — Python 2 exception syntax was re-introduced across five sites [medium, S] — `correctness.md`
9. **CORR-045** — Late job-log subscriber can hang forever [medium, M] — `correctness.md`
10. **CORR-046** — Public job responses omit per-artifact metadata [medium, S] — `correctness.md`

## Quick wins

1. **ARCH-011** — correct the worker SDK import-time documentation [medium, S] — `architecture.md`
2. **ARCH-012** — remove hardcoded route selection [medium, S] — `architecture.md`
3. **ARCH-028** — unify plan and orchestrator data roots [medium, S] — `architecture.md`
4. **ARCH-029** — move cleanup response models to the core schema boundary [medium, S] — `architecture.md`
5. **CORR-015** — make edge route registration complete [medium, S] — `correctness.md`
6. **CORR-034** — restore the repository's consistent exception syntax [medium, S] — `correctness.md`
7. **CORR-046** — expose or explicitly contract artifact metadata [medium, S] — `correctness.md`
8. **CORR-047** — return measured RunPod pricing when a valid rate is available [medium, S] — `correctness.md`
9. **DOC-004** — document the granite_speech worker consistently [medium, S] — `surface.md`
10. **DOC-014** — document the actual administrative CLI namespaces [medium, S] — `surface.md`

## Story Counts

| Status | Count |
|---|---|
| open | 29 |
| in-progress | 0 |
| fixed | 61 |
| verified | 175 |
| stale | 11 |
| wontfix | 0 |
| **total filed** | **276** |

## Changes Since Last Review

The refresh covers `49747dd..22d20f5`: 149 substantive changed or added paths, excluding the prior `docs/code_review/` output. The worktree was fast-forwarded to current `master` before scanning. The delta adds public/admin API boundaries, request/error/path validation, job retention and cost surfaces, TLS and worker authentication, dashboard/version/cost views, UX-review tooling and evidence, simulation changes, worker SDK bounds, and corresponding unit/integration tests. New findings were added for RunPod pricing, Redis archive persistence, cleanup schema coupling, private helper imports, route-module growth, retention exception handling, dashboard response validation, polling and transport-cache costs, multipart memory use, audit durability, input-deletion races, and administrative CLI documentation. Mutable stories were re-resolved at the current commit; fixed, verified, and wontfix stories were preserved.

## Last orientation snapshot

**Repository**: `acheron`, a FastAPI orchestrator for asynchronous audio transformation with HTTP/gRPC workers, local handlers, and Redis or in-memory stores.

**Branch / HEAD**: `docs/code-review-refresh` at `22d20f5028d64c8fdac61ad9c7871397c7cf178e` (aligned with `master`).

**Top-level layout**: `src/` contains `acheron`; `tests/` mirrors core, shell, worker SDK, integration, first-run, simulation, scripts, and UX-review surfaces; `dashboard/`, `workers/`, `stubs/`, `proto/`, `sim/`, `compose/`, `scripts/`, and `docs/` provide supporting applications, deployment, tooling, and plans/specs.

**Boundaries**: `src/acheron/` contains `core/`, `shell/`, `worker_sdk/`, `ux_review/`, and `proto/`; no `application/`, `infrastructure/`, `models/`, `macros/`, or `ports.py` layer exists. Import-linter contracts govern core/shell, worker-sdk/shell, and workers/shell direction.

**Tests**: Mirrors exist under `tests/core`, `tests/shell`, `tests/worker_sdk`, `tests/integration`, `tests/first_run`, `tests/sim`, `tests/scripts`, and `tests/ux_review`.

**Tooling**: `just validate` runs lint, type-check, and tests; `just ux-validate`, `just first-run`, and `just sim-run` provide UX and deployment-specific checks. `pyproject.toml` defines the Acheron CLI and worker-edge entry points, Ruff, mypy, basedpyright, pytest, import-linter, and uv workspace configuration.

**dbt**: No root dbt project, `models/`, or `macros/` layer is present.

**Entry points**: `acheron = acheron.cli:main`; `acheron-worker-edge = acheron.worker_sdk.cli:main`; dashboard, worker, simulation, and deployment recipes provide additional operational entry points.

**Changed top-level directories**: `dashboard/`, `docs/`, `sim/`, `src/`, `stubs/`, `tests/`, and `workers/`. Top-level files `.env.example`, `.gitignore`, `Dockerfile`, `Dockerfile.edge`, `README.md`, and `docker-compose.yml` also changed. Tracked file counts increased in `dashboard` (+1), `docs` (+2), `src` (+8), and `tests` (+8).
