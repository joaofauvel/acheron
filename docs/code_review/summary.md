---
branch: docs/code-review-refresh
initial_review_commit: 23c29e1
last_updated_commit: 49747dd53a5c4114dc2ac82452315bd8502c34a3
last_staleness_scan:
  commit: 49747dd53a5c4114dc2ac82452315bd8502c34a3
  date: 2026-07-30
---

# Code Review Summary

## Per-theme grades

| Theme | Grade | Stories (open/in-progress/stale by severity) |
|---|---|---|
| ARCH | B | 0 critical, 1 high, 3 medium, 0 low |
| CFG | A | 0 critical, 0 high, 0 medium, 0 low |
| CORR | B | 0 critical, 0 high, 4 medium, 0 low |
| DATA | A | 0 critical, 0 high, 0 medium, 1 low |
| DOC | A | 0 critical, 0 high, 1 medium, 0 low |
| DX | B | 0 critical, 1 high, 2 medium, 0 low |
| EXC | A | 0 critical, 0 high, 1 medium, 0 low |
| MAINT | A | 0 critical, 0 high, 1 medium, 1 low |
| OBS | A | 0 critical, 0 high, 0 medium, 0 low |
| PERF | A | 0 critical, 0 high, 1 medium, 1 low |
| PKG | A | 0 critical, 0 high, 0 medium, 0 low |
| REPRO | A | 0 critical, 0 high, 0 medium, 1 low |
| SEC | A | 0 critical, 0 high, 0 medium, 3 low |
| TEST | B | 0 critical, 0 high, 4 medium, 0 low |
| TYPE | A | 0 critical, 0 high, 0 medium, 1 low |
| ML | A | 0 critical, 0 high, 0 medium, 0 low |
| MATH | A | 0 critical, 0 high, 0 medium, 0 low |

## Top Concerns

1. **ARCH-027** — Remote workers cannot read upstream manifests from the orchestrator's cache [high, M] — `architecture.md`
2. **DX-008** — Dashboard output links may expose an internal orchestrator hostname [high, M] — `surface.md`
3. **ARCH-011** — `worker_sdk/__init__.py` docstring falsely claims the module is GPU-SDK-free at import time [medium, S] — `architecture.md`
4. **ARCH-012** — `create_worker_app` cherry-picks routes from `EdgeApp.app.routes` via a hardcoded `inner_paths` set [medium, S] — `architecture.md`
5. **ARCH-028** — Plan cache and orchestrator data roots can diverge [medium, S] — `architecture.md`
6. **CORR-015** — `create_worker_app` silently drops routes added outside `inner_paths` [medium, S] — `correctness.md`
7. **CORR-034** — Python 2 exception syntax was re-introduced across five sites [medium, S] — `correctness.md`
8. **CORR-045** — Late job-log subscriber can hang forever [medium, M] — `correctness.md`
9. **CORR-046** — Public job responses omit per-artifact metadata [medium, S] — `correctness.md`
10. **DOC-004** — README architecture tree, CI, and test paths omit the granite_speech worker [medium, S] — `surface.md`

## Quick Wins

1. **ARCH-011** — correct the worker SDK import-time documentation [medium, S] — `architecture.md`
2. **ARCH-012** — remove hardcoded route selection [medium, S] — `architecture.md`
3. **ARCH-028** — unify plan and orchestrator data roots [medium, S] — `architecture.md`
4. **CORR-015** — make edge route registration complete [medium, S] — `correctness.md`
5. **CORR-034** — restore the repository's consistent exception syntax [medium, S] — `correctness.md`
6. **CORR-046** — expose or explicitly contract artifact metadata [medium, S] — `correctness.md`
7. **DOC-004** — document the granite_speech worker consistently [medium, S] — `surface.md`
8. **DX-009** — include UX rubric validation in the documented final gate [medium, S] — `surface.md`
9. **DX-010** — make dry-run upload side effects explicit or remove them [medium, S] — `surface.md`
10. **EXC-006** — narrow the optional-warning exception boundary [medium, S] — `code-quality.md`

## Story Counts

| Status | Count |
|---|---|
| open | 19 |
| in-progress | 0 |
| fixed | 61 |
| verified | 175 |
| stale | 8 |
| wontfix | 0 |
| **total filed** | **263** |

## Changes Since Last Review

The refresh covers `a749f8f..49747dd`: 152 changed files, including the public output-download contract, descriptor-pinned artifact serving, structured preview errors, dashboard/CLI output links, UX rubric refreshes, cache canonicalization, and expanded API, transport, and event tests. Existing fixed, verified, and wontfix stories were preserved; open and stale stories remain tracked with current review metadata. New findings were added for the event subscription race, cache wiring, public error/path handling, output integrity metadata, remote dashboard URLs, and validation/coverage gaps.

## Last Orientation Snapshot

**Repository**: `acheron`, a FastAPI orchestrator for asynchronous audio transformation with HTTP/gRPC workers, local handlers, and Redis or in-memory stores.

**Branch / HEAD**: `docs/code-review-refresh` at `49747dd53a5c4114dc2ac82452315bd8502c34a3`.

**Top-level layout**: `src/acheron/core/` contains domain models and interfaces; `src/acheron/shell/` contains orchestration, API, stores, transports, health, cache, and configuration; `src/acheron/worker_sdk/` contains worker edge/runtime code; `dashboard/`, `workers/`, `stubs/`, `tests/`, `scripts/`, `proto/`, `sim/`, `compose/`, and `.github/` provide supporting packages and tooling.

**Boundaries**: No `application/`, `infrastructure/`, `models/`, or `macros/` directory exists. No `ports.py` files were found. Import-linter contracts cover core/shell, worker-sdk/shell, and workers/shell boundaries.

**Tests**: Mirrors exist under `tests/core`, `tests/shell`, `tests/worker_sdk`, `tests/integration`, `tests/first_run`, `tests/sim`, and `tests/scripts`.

**Tooling**: `just validate` runs lint, type-check, and tests; `just ux-validate`, `just first-run`, and `just sim-run` provide UX and deployment-specific checks. `pyproject.toml` defines the `acheron` CLI entry point, Ruff, mypy, basedpyright, pytest, import-linter, and uv workspace configuration.

**Entry points**: `acheron = acheron.cli:main`; worker SDK and dashboard applications are exposed through their package modules and deployment recipes. No dbt model or macro layer is present.
