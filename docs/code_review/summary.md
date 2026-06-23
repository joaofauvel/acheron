---
branch: chore/code-review-update
initial_review_commit: 23c29e1
last_updated_commit: dbec2be
last_staleness_scan:
  commit: dbec2be
  date: 2026-06-23
---

# Code Review Summary

## Per-theme grades

| Theme | Grade | Stories (open/in-progress/stale) |
|---|---|---|
| CORR | B | 0 critical, 1 high, 4 medium, 4 low |
| ML | A | 0 critical, 0 high, 0 medium, 0 low |
| MATH | A | 0 critical, 0 high, 0 medium, 0 low |
| ARCH | B | 0 critical, 0 high, 4 medium, 3 low |
| CFG | B | 0 critical, 0 high, 5 medium, 0 low |
| MAINT | B | 0 critical, 0 high, 4 medium, 7 low |
| EXC | A | 0 critical, 0 high, 2 medium, 1 low |
| TYPE | A | 0 critical, 0 high, 2 medium, 5 low |
| TEST | A | 0 critical, 0 high, 2 medium, 3 low |
| REPRO | A | 0 critical, 0 high, 1 medium, 1 low |
| DATA | A | 0 critical, 0 high, 2 medium, 1 low |
| PERF | B | 0 critical, 0 high, 4 medium, 0 low |
| OBS | B | 0 critical, 0 high, 4 medium, 2 low |
| SEC | C | 1 critical, 3 high, 2 medium, 5 low |
| DX | A | 0 critical, 0 high, 2 medium, 0 low |
| PKG | A | 0 critical, 0 high, 1 medium, 1 low |
| DOC | A | 0 critical, 0 high, 1 medium, 0 low |

## Top concerns

1. SEC-008 — Auto-generated registration token is logged in plaintext at startup [critical] — `operations.md`
2. SEC-007 — Host Path Traversal & Arbitrary Local File Read in ExtractionHandler [high] — `operations.md`
3. SEC-009 — Registration token file created with process umask (potentially world-readable) [high] — `operations.md`
4. SEC-011 — `ACHERON_REGISTRATION_TOKEN` defaults to publicly-known `dev-registration-token` in compose and `.env.example` [high] — `operations.md`
5. CORR-014 — `RunPodClient.run` silently treats a FAILED RunPod job as a successful empty result [high] — `correctness.md`
6. ARCH-009 — HealthProvider ABC lives in shell/health_providers.py instead of core/interfaces.py [medium] — `architecture.md`
7. CFG-003 — `ACHERON_OPEN_REGISTRATION` read directly in deps.py, bypassing the new settings loader [medium] — `architecture.md`
8. CFG-004 — Orchestrator mutates `Settings.orchestrator.data_dir` in-place from two call sites [medium] — `architecture.md`
9. CFG-005 — `${VAR}` env-var expansion silently substitutes unset env vars as empty strings, disabling providers [medium] — `architecture.md`
10. CFG-006 — Env vars read outside the project's settings loaders — 5 new sites in transports and worker_sdk [medium] — `architecture.md`

## Quick wins

1. SEC-008 — Auto-generated registration token is logged in plaintext at startup [critical, S effort] — `operations.md`
2. SEC-009 — Registration token file created with process umask (potentially world-readable) [high, S effort] — `operations.md`
3. SEC-011 — `ACHERON_REGISTRATION_TOKEN` defaults to publicly-known `dev-registration-token` in compose and `.env.example` [high, S effort] — `operations.md`
4. CORR-014 — `RunPodClient.run` silently treats a FAILED RunPod job as a successful empty result [high, S effort] — `correctness.md`
5. ARCH-009 — HealthProvider ABC lives in shell/health_providers.py instead of core/interfaces.py [medium, S effort] — `architecture.md`
6. CFG-003 — `ACHERON_OPEN_REGISTRATION` read directly in deps.py, bypassing the new settings loader [medium, S effort] — `architecture.md`
7. CFG-004 — Orchestrator mutates `Settings.orchestrator.data_dir` in-place from two call sites [medium, S effort] — `architecture.md`
8. CFG-005 — `${VAR}` env-var expansion silently substitutes unset env vars as empty strings, disabling providers [medium, S effort] — `architecture.md`
9. CFG-006 — Env vars read outside the project's settings loaders — 5 new sites in transports and worker_sdk [medium, S effort] — `architecture.md`
10. CFG-007 — `WorkerSettings.model_id` and `WorkerSettings.output_mode` are config knobs that don't actually control anything [medium, S effort] — `architecture.md`
11. ARCH-011 — `worker_sdk/__init__.py` docstring falsely claims the module is GPU-SDK-free at import time [medium, S effort] — `architecture.md`
12. ARCH-012 — `create_worker_app` cherry-picks routes from `EdgeApp.app.routes` via a hardcoded `inner_paths` set [medium, S effort] — `architecture.md`
13. CORR-009 — Step handler caches worker list and worker instances across steps and plans [medium, S effort] — `correctness.md`
14. CORR-010 — `${VAR}` env-var expansion silently substitutes missing variables with empty string [medium, S effort] — `correctness.md`
15. CORR-013 — `_parse_multipart` discards per-part `X-Acheron-Metadata` header sent by the SDK edge [medium, S effort] — `correctness.md`
16. CORR-015 — `create_worker_app` cherry-picks routes from `EdgeApp` via hardcoded `inner_paths`; new routes silently dropped [medium, S effort] — `correctness.md`
17. MAINT-006 — Orchestrator.start() inlines 17-line registration-token block; logs the token in plaintext [medium, S effort] — `code-quality.md`
18. MAINT-007 — RunPodHealthProvider and HuggingFaceHealthProvider duplicate the HTTP fetch envelope [medium, S effort] — `code-quality.md`
19. MAINT-011 — `create_worker_app` builds an `EdgeApp` only to copy its routes onto the outer app via path-string matching; the inner `EdgeApp` is dead code [medium, S effort] — `code-quality.md`
20. PERF-006 — Edge `/execute` buffers entire multipart body in memory; O(n²) append for FileArtifact streams [medium, S effort] — `operations.md`
21. PERF-007 — Per-call `httpx.AsyncClient` construction in health probes and pricing refresh (no connection reuse) [medium, S effort] — `operations.md`
22. OBS-006 — `RunPodClient` and `RunPodPrice` swallow transport / API errors with no log line [medium, S effort] — `operations.md`
23. OBS-007 — Edge `/execute` endpoint is unauthenticated; `docker-compose` exposes it on host network (8004:8001) [medium, S effort] — `operations.md`
24. DATA-006 — `HttpWorker._parse_multipart` edge cases (no metrics part, missing boundary, non-multipart body) are not covered [medium, S effort] — `verification.md`
25. DX-003 — `just install` does not install the new `workers/qwen3tts/` workspace member, breaking the documented fresh-clone setup [medium, S effort] — `surface.md`
26. PKG-003 — `Dockerfile:39` (certs-init stage) pins `cryptography~=49.0` while `pyproject.toml:168` pins `cryptography~=46.0` [medium, S effort] — `surface.md`
27. SEC-013 — `RunPodPrice` sends API key as URL query parameter instead of Authorization header [medium, S effort] — `operations.md`
28. SEC-014 — `worker.edge.yaml` default `orchestrator_url` is HTTP — registration token sent in cleartext when env var is not overridden [medium, S effort] — `operations.md`

## Story counts

| Status | Count |
|---|---|
| open | 77 |
| in-progress | 0 |
| fixed | 0 |
| verified | 43 |
| stale | 0 |
| wontfix | 0 |

## Changes since last review

The diff `63faed4..dbec2be` covers the close-out of Layer 8a (the qwen3tts RunPod serverless worker, the worker_sdk subpackage, the SDK-backed stub matrix, the transport `_multipart` refactor, and the proto `OutputChunk.Artifact` oneof), plus the GHCR publish workflow for the new worker images. 38 new findings surfaced: 5 CORR, 3 ARCH, 2 CFG, 6 MAINT, 1 EXC, 4 TYPE, 1 TEST, 1 REPRO, 2 DATA, 2 PERF, 3 OBS, 5 SEC, 1 DX, 2 PKG. No previously verified stories regressed; 10 open carry-overs (ARCH-008, MAINT-002, MAINT-005, MAINT-006, MAINT-007, MAINT-008, EXC-001, EXC-003, TYPE-001, TYPE-003, TYPE-004) were re-resolved against the new line numbers. The 6 open carry-overs that were not re-resolved (CORR-009, CORR-010, CORR-011, CORR-012, REPRO-001, DATA-005, all SEC-005..010, PERF-004, PERF-005, OBS-001, OBS-003, OBS-005, MAINT-002, MAINT-005) cite code that is unmodified since 63faed4 and remain valid at dbec2be.

Dominant themes:

- **SEC (C, 1 critical / 3 high / 2 medium / 4 low)** — unchanged grade but new findings deepen the same problem: SEC-011 (high) is the publicly-known `dev-registration-token` default in compose and `.env.example`; SEC-013 (medium) sends the RunPod API key as a URL query parameter; SEC-014 (medium) ships `worker.edge.yaml` with `orchestrator_url: http://...` so the bearer token is sent in cleartext by default; SEC-015 (low) is the long-standing missing `USER` directive across all three Dockerfiles. SEC-008 / SEC-009 (the auto-gen token log + file-mode) remain critical/high; SEC-007 (extraction path traversal) remains high.
- **CORR (B)** — CORR-014 (high) is the most significant new finding: `RunPodClient.run` never inspects `output.status`, so a FAILED RunPod job (model OOM, GPU missing, cold-start timeout) propagates as a successful empty `/execute` response. CORR-013 (medium) is the symmetric gap in the orchestrator's HTTP path: `_parse_multipart` discards the per-part `X-Acheron-Metadata` header, losing per-chunk ordering info. CORR-015 / CORR-016 (the route cherry-pick and the false "GPU-SDK free" claim) are two distinct lenses on the same `worker_sdk/__init__.py` shape.
- **MAINT (B, 6 new)** — Most are low-severity pattern smells introduced by the new SDK: Python 2-style `except A, B:` at 7 sites (MAINT-009), the duplicate docstring (MAINT-010), the dead `EdgeApp` instance built only to copy its routes (MAINT-011), the hand-listed `WorkerCapabilities` field copies (MAINT-012, MAINT-013), and the redundant no-op startup/shutdown overrides in the stub handlers (MAINT-014).
- **OBS (B)** — OBS-007 (medium) is the highest-impact new OBS: the edge `/execute` endpoint is unauthenticated AND `docker-compose.yml` maps it to the host on 8004:8001, exposing the entire cost-bearing surface. OBS-006 (medium) extends the OBS-005 anti-pattern to the new RunPod client + pricing module (silent error swallowing).
- **PERF (B)** — PERF-006 (medium) is the multipart-body O(n²) buffering that defeats the `StreamArtifact` design at any non-trivial size; PERF-007 (medium) is the per-call `httpx.AsyncClient` construction across the health probes and pricing refresh — same root cause, multiple sites.
- **CFG (B)** — CFG-006 (medium) bundles 5 new env-var reads in the new transports and worker_sdk that bypass the settings loaders (the same pattern as CFG-003/004/005); CFG-007 (medium) is the `model_id` / `output_mode` knobs in `WorkerSettings` that no code consumes.
- **ARCH (B)** — ARCH-011 (medium) and ARCH-012 (medium) are the two `worker_sdk/__init__.py` / `create_worker_app` structural smells (false import-time claim + route cherry-pick). ARCH-013 (low) is the same DRY pattern the previous review flagged for the store factories, now in the transport layer.

Three pending SHA placeholders (MAINT-002, EXC-001, REPRO-001) could not be resolved — `git log --grep='(<ID>)' --oneline` returned no commits in this delta. They remain `pending` per update-mode rules. No stories were marked `stale` (the cited code still exists at re-resolved line numbers).

Grades: 11 themes at A (ML, MATH, EXC, TYPE, TEST, REPRO, DATA, DX, PKG, DOC, unchanged), 4 themes at B (CORR, ARCH, MAINT, PERF, OBS all new at B; CFG unchanged), 1 theme at C (SEC — driven by SEC-008 critical). No aggregate codebase grade (per the rubric).

## Last orientation snapshot

**Repository**: acheron — audiobook processing pipeline (FastAPI orchestrator + gRPC/HTTP workers + Redis/memory stores). Greenfield (per AGENTS.md).

**Branch / HEAD**: `chore/code-review-update` at `dbec2be3e099e01c26b766f15bc55390905dfdf8`.

**Top-level layout**: `src/acheron/core/` (domain models, errors, chunking, planner, interfaces), `src/acheron/shell/` (orchestrator, API, executors: streaming/async/sequential, stores: memory/redis, transports: http/grpc/local, cache, health, TLS, step_handler, local_handlers, capabilities, health_providers, config), `src/acheron/worker_sdk/` (NEW: base SDK for building workers — config_loader, _edge_http, _runpod_client, registration, pricing, artifacts, cloud, handler, app, cli, settings, schemas), `dashboard/` (separate package), `stubs/` (NEW: 3 generic SDK-backed stubs + _sdk_base + nltk mock — replaced 7 per-role stubs), `workers/qwen3tts/` (NEW: RunPod serverless TTS worker, uv workspace member), `tests/` (mirrors src: tests/core, tests/shell, tests/worker_sdk, tests/integration, tests/scripts; plus stubs/tests, dashboard/tests, workers/qwen3tts/tests).

**No hexagonal layers**: flat package structure. Interfaces (ABCs) in `core/interfaces.py`. No `ports.py` files.

**Boundaries** (enforced by import-linter): `core` must NOT import `shell`; `worker_sdk` must NOT import `shell`; `workers` must NOT import `shell`.

**Test landscape**: tests/core/, tests/shell/ (api/, stores/, transports/), tests/worker_sdk/ (NEW — 15 test files mirroring 13 source modules), tests/integration/, tests/scripts/. New since last review: full worker_sdk test module, partials API tests, expanded orchestrator/config/health_monitor tests, qwen3tts tests with `_FakeModel` pattern, stubs/tests/test_stubs_healthy.py parameterizing the 7-stub matrix.

**Tooling**: `just certs install lint-imports lint-strict proto test type-check type-check-pyright validate`. All deps `~=` pinned. jinja2 in optional-dependencies[dashboard]. uv workspace member: `workers/qwen3tts`.

**Key entry points**: `acheron.cli:main`, `acheron.worker_sdk.cli:main` (`acheron-worker-edge`), `acheron.shell.api.__main__`, `acheron.shell.api.app:create_app`.
