---
branch: fix/code-review-medium-high
initial_review_commit: 23c29e1
last_updated_commit: 5ba5e48
last_staleness_scan:
  commit: 5ba5e48
  date: 2026-08-01
---

# Code Review Summary

## Per-theme grades

| Theme | Grade | Stories (open/in-progress/stale by severity) |
|---|---|---|
| ARCH | B | 0 critical, 0 high, 2 medium, 1 low |
| CFG | A | 0 critical, 0 high, 0 medium, 0 low |
| CORR | B | 0 critical, 0 high, 2 medium, 1 low |
| DATA | A | 0 critical, 0 high, 0 medium, 1 low |
| DOC | A | 0 critical, 0 high, 1 medium, 0 low |
| DX | A | 0 critical, 0 high, 1 medium, 0 low |
| EXC | A | 0 critical, 0 high, 0 medium, 0 low |
| MAINT | A | 0 critical, 0 high, 0 medium, 1 low |
| OBS | A | 0 critical, 0 high, 0 medium, 0 low |
| PERF | B | 0 critical, 0 high, 0 medium, 1 low |
| PKG | A | 0 critical, 0 high, 0 medium, 0 low |
| REPRO | A | 0 critical, 0 high, 0 medium, 1 low |
| SEC | A | 0 critical, 0 high, 0 medium, 4 low |
| TEST | B | 0 critical, 0 high, 1 medium, 0 low |
| TYPE | A | 0 critical, 0 high, 0 medium, 2 low |
| ML | A | 0 critical, 0 high, 0 medium, 0 low |
| MATH | A | 0 critical, 0 high, 0 medium, 0 low |

## Top Concerns

No open high- or medium-severity code-review stories remain.

## Quick wins

1. **ARCH-030** — remove module-private helper imports across shell boundaries [low, S] — `architecture.md`
2. **CORR-048** — preserve Redis archive markers during job updates [low, S] — `correctness.md`
3. **DATA-011** — enforce or document persisted output integrity fields [low, S] — `verification.md`
4. **REPRO-007** — cover cache CWD semantics [low, S] — `verification.md`
5. **TYPE-015** — keep health response parsing behind the typed boundary [low, S] — `code-quality.md`

## Story Counts

| Status | Count |
|---|---|
| open | 7 |
| in-progress | 0 |
| fixed | 61 |
| verified | 196 |
| stale | 12 |
| wontfix | 0 |
| **total filed** | **276** |

## Changes Since Last Review

The review was refreshed through `5ba5e48`, following the original `49747dd..22d20f5` scan. Since that scan, the medium/high tackle branch fixed cache propagation, job-event lifecycle cleanup, jobs-route decomposition, shared cleanup-schema ownership, per-artifact output metadata, retention exception classification, durable administrative audits, dashboard URL separation, combined cost polling, measured RunPod pricing, spooled multipart input handling, nested output-directory symlink coverage, malformed-WAV rejection coverage, administrative CLI documentation, and validation-gate documentation; related citations, statuses, and summary counts were re-resolved. The remaining open and stale stories are listed above.

The UX rubric remains a separate required pre-merge gate; its current checkout reports 29 metadata errors: 16 pre-existing attestation mismatches plus 13 later citation drifts. It requires a rubric refresh before merge.

## Last orientation snapshot

**Repository**: `acheron`, a FastAPI orchestrator for asynchronous audio transformation with HTTP/gRPC workers, local handlers, and Redis or in-memory stores.

**Branch / HEAD**: `fix/code-review-medium-high` at `5ba5e48` (implementation commit; review-metadata follow-up may be newer).

**Top-level layout**: `src/` contains `acheron`; `tests/` mirrors core, shell, worker SDK, integration, first-run, simulation, scripts, and UX-review surfaces; `dashboard/`, `workers/`, `stubs/`, `proto/`, `sim/`, `compose/`, `scripts/`, and `docs/` provide supporting applications, deployment, tooling, and plans/specs.

**Boundaries**: `src/acheron/` contains `core/`, `shell/`, `worker_sdk/`, `ux_review/`, and `proto/`; no `application/`, `infrastructure/`, `models/`, `macros/`, or `ports.py` layer exists. Import-linter contracts govern core/shell, worker-sdk/shell, and workers/shell direction.

**Tests**: Mirrors exist under `tests/core`, `tests/shell`, `tests/worker_sdk`, `tests/integration`, `tests/first_run`, `tests/sim`, `tests/scripts`, and `tests/ux_review`.

**Tooling**: `just validate` runs lint, type-check, and tests; `just ux-validate`, `just first-run`, and `just sim-run` provide UX and deployment-specific checks. `pyproject.toml` defines the Acheron CLI and worker-edge entry points, Ruff, mypy, basedpyright, pytest, import-linter, and uv workspace configuration.

**dbt**: No root dbt project, `models/`, or `macros/` layer is present.

**Entry points**: `acheron = acheron.cli:main`; `acheron-worker-edge = acheron.worker_sdk.cli:main`; dashboard, worker, simulation, and deployment recipes provide additional operational entry points.

**Changed top-level directories**: `dashboard/`, `docs/`, `sim/`, `src/`, `stubs/`, `tests/`, and `workers/`. Top-level files `.env.example`, `.gitignore`, `Dockerfile`, `Dockerfile.edge`, `README.md`, and `docker-compose.yml` also changed. Tracked file counts increased in `dashboard` (+1), `docs` (+2), `src` (+8), and `tests` (+8).
