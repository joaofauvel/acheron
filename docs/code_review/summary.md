---
branch: master
initial_review_commit: 23c29e1
last_updated_commit: 36294f8
last_staleness_scan:
  commit: 36294f8
  date: 2026-06-24
---

# Code Review Summary

## Per-theme grades

| Theme | Grade | Stories (open/in-progress/stale) |
|---|---|---|
| CORR | B | 0 critical, 0 high, 7 medium, 7 low |
| ML | A | 0 critical, 0 high, 0 medium, 0 low |
| MATH | A | 0 critical, 0 high, 0 medium, 0 low |
| ARCH | B | 0 critical, 0 high, 8 medium, 4 low |
| CFG | B | 0 critical, 0 high, 8 medium, 1 low |
| MAINT | B | 0 critical, 0 high, 6 medium, 8 low |
| EXC | B | 0 critical, 0 high, 3 medium, 1 low |
| TYPE | A | 0 critical, 0 high, 2 medium, 7 low |
| TEST | B | 0 critical, 0 high, 6 medium, 8 low |
| REPRO | A | 0 critical, 0 high, 1 medium, 1 low |
| DATA | A | 0 critical, 0 high, 2 medium, 1 low |
| PERF | B | 0 critical, 0 high, 4 medium, 1 low |
| OBS | B | 0 critical, 0 high, 3 medium, 3 low |
| SEC | B | 0 critical, 0 high, 3 medium, 7 low |
| DX | A | 0 critical, 0 high, 1 medium, 0 low |
| PKG | A | 0 critical, 0 high, 1 medium, 1 low |
| DOC | A | 0 critical, 0 high, 2 medium, 1 low |

Grade changes vs `eb6849c`: CORR C→B (high count 1→0; medium count 11→7, crosses back under 9-15 medium threshold); SEC C→B (critical count 1→0, high count 2→0); DATA B→A (medium count 4→2, crosses back under ≤2 medium threshold); DOC B→A (medium count 3→2, crosses back under ≤2 medium threshold). All other themes unchanged. **No themes at C now** — the previous C-drivers (SEC-008 critical, CORR-014 high, the 8c-added 11-medium burst in CORR, the 8c-added 3 mediums in DOC) are all resolved.

## Top concerns

1. CORR-018 — ASR multipart path materializes entire audio file in memory before streaming [medium, M] — `correctness.md`
2. CORR-019 — SDK edge `_parse_multipart_request` materializes entire body in memory [medium, M] — `correctness.md`
3. CORR-029 — `TranslateGemmaRunpodHandler._translate_batch` has no partial-success handling; mid-batch failure discards all completed work [medium, M] — `correctness.md`
4. ARCH-014 — `HttpWorker.execute()` branches on `WorkerType.ASR` to dispatch to a typed handler [medium, M] — `architecture.md`
5. ARCH-020 — `HttpWorker._execute_with_upstream_input` has a leaky triple-magic-string signature shared by three call sites [medium, M] — `architecture.md`
6. TEST-015 — `src/acheron/tls.py` (new top-level module, 114 lines) has no direct unit tests [medium, M] — `verification.md`
7. TEST-014 — `workers/translategemma/tests/test_handler.py` does not cover the model.generate error path, partial-success, or pad_token_id init [medium, M] — `verification.md`
8. MAINT-002 — `redis.py` hand-rolls JSON ser/deser for domain models that `cache.py` serializes via pydantic, duplicating and drifting [medium, M] — `code-quality.md`
9. TYPE-001 — `cheron_handler` factory parameter shadows the type name [medium, M] — `code-quality.md`
10. TYPE-003 — `submit_job` accepts `Job` and returns `Job`; the producer/consumer relationship is hidden in the type [medium, M] — `code-quality.md`
11. TYPE-008 — `_dispatch` and `_validate_bearer` return `dict`; should return typed protocol [medium, M] — `code-quality.md`
12. TYPE-010 — All three RunPod worker handlers type self._model/self._processor as `Any` with a stale-prone impl-phase comment [low, M] — `code-quality.md`
13. REPRO-001 — `_dispatch` catches bare `Exception` instead of bare `BaseException` (regression of EXC-001 retry pattern) [medium, M] — `correctness.md`
14. SEC-005 — Worker registration fails open when the orchestrator's `Settings.orchestrator.open_registration` is `True` [medium, S] — `operations.md`
15. SEC-006 — Worker's `/execute` accepts requests without `Authorization` header; the bearer check is conditional on a config flag [medium, S] — `operations.md`
16. EXC-001 — tenacity dependency is unused; `WorkerTimeoutError`/`PlanValidationError` are never raised [medium, M] — `code-quality.md`
17. CORR-015 — `create_worker_app` cherry-picks routes from `EdgeApp` via hardcoded `inner_paths` [medium, S] — `correctness.md`
18. ARCH-009 — `HealthProvider` ABC lives in `shell/health_providers.py` instead of `core/interfaces.py` [medium, S] — `architecture.md`
19. ARCH-012 — `create_worker_app` cherry-picks routes from `EdgeApp.app.routes` via hardcoded `inner_paths` [medium, S] — `architecture.md`
20. CFG-003 — `ACHERON_OPEN_REGISTRATION` read directly in `deps.py`, bypassing the new settings loader [medium, S] — `architecture.md`

## Quick wins

1. SEC-005 — Worker registration fails open when `Settings.orchestrator.open_registration` is `True` [medium, S] — `operations.md`
2. SEC-006 — Worker's `/execute` accepts requests without `Authorization` header [medium, S] — `operations.md`
3. CORR-015 — `create_worker_app` cherry-picks routes from `EdgeApp` [medium, S] — `correctness.md`
4. ARCH-009 — `HealthProvider` ABC lives in `shell/health_providers.py` [medium, S] — `architecture.md`
5. ARCH-011 — `worker_sdk/__init__.py` docstring falsely claims the module is GPU-SDK-compatible [medium, S] — `architecture.md`
6. ARCH-012 — `create_worker_app` cherry-picks routes from `EdgeApp.app.routes` [medium, S] — `architecture.md`
7. ARCH-015 — `step_cache` is threaded through `default_worker_factory` even though it's only used in the local path [medium, S] — `architecture.md`
8. ARCH-019 — `validate_chunking_fits_workers` is a post-step in `submit_job` that should fold into `compile_plan` [medium, S] — `architecture.md`
9. ARCH-021 — Identical uvicorn+TLS 7-line boilerplate duplicated across 4 entry points [medium, S] — `architecture.md`
10. CFG-003 — `ACHERON_OPEN_REGISTRATION` read directly in `deps.py` [medium, S] — `architecture.md`
11. CFG-004 — Orchestrator mutates `Settings.orchestrator.data_dir` in-place from two call sites [medium, S] — `architecture.md`
12. CFG-005 — `${VAR}` env-var expansion silently substitutes unset env vars as empty strings [medium, S] — `architecture.md`
13. CFG-006 — Env vars read outside the project's settings loaders — 5 new sites in 8c [medium, S] — `architecture.md`
14. CFG-007 — `WorkerSettings.model_id` and `WorkerSettings.output_mode` are config but read from hard-coded constants [medium, S] — `architecture.md`
15. CFG-008 — CFG-007 regression: `WorkerSettings.model_id` is set in 4 YAML files but not consumed [medium, S] — `architecture.md`
16. CFG-009 — `Settings.chars_per_token` is a top-level knob consumed by exactly one function [medium, S] — `architecture.md`
17. CFG-010 — `WorkerSettings.model_id` is now consumed only by `translategemma` [medium, S] — `architecture.md`
18. CORR-009 — Step handler caches worker list and worker instances across steps [medium, S] — `correctness.md`
19. CORR-010 — `${VAR}` env-var expansion silently substitutes missing variables with empty string [medium, S] — `correctness.md`
20. CORR-020 — `make_runpod_handler` silently coerces missing `data` field to empty bytes [medium, S] — `correctness.md`
21. DATA-005 — `RedisWorkerStore._deserialize_worker` invalid status field has no corrupted-record path [medium, S] — `verification.md`
22. DATA-009 — `tests/core/test_planner.py:TestValidateChunkingFitsWorkers` has no boundary-condition test [medium, S] — `verification.md`
23. DOC-003 — Configuration docs drift across README, .env.example, and an undocumented env-var [medium, S] — `surface.md`
24. DOC-004 — README architecture tree, CI section, and Test paths omit the new `granite_speech` and `translategemma` workspaces [medium, S] — `surface.md`
25. DX-003 — `just install` does not install the new `workers/qwen3tts/` workspace members [medium, S] — `surface.md`
26. EXC-004 — `create_worker_app` lifespan catches bare `BaseException` for the eager price refresh [medium, S] — `code-quality.md`
27. EXC-005 — `_edge_http.py` `_dispatch` catches bare `BaseException` for handler failures [medium, S] — `code-quality.md`
28. OBS-001 — Health probe path silent on all retryable failures — operator cannot confirm probe is running [medium, S] — `operations.md`
29. OBS-003 — `submit_job` and the new `validate_chunking_fits_workers` post-step have no log line on success [medium, S] — `operations.md`
30. OBS-005 — `orchestrator.start()` logs no aggregate "ready" line; 8 separate `logger.info` calls during wiring [medium, S] — `operations.md`
31. PERF-004 — `Plan` dataclass builds full call graph in `compile_plan` even when the request is rejected at validation [medium, S] — `operations.md`
32. PERF-005 — `submit_job` re-runs the type adapter for `Job` twice per request [medium, S] — `operations.md`
33. PERF-006 — ASR multipart path materializes the entire `multipart/form-data` body in memory [medium, S] — `operations.md`
34. TEST-002 — `_step_cache_key` is in `cache.py` but has no direct unit test [medium, S] — `verification.md`
35. TEST-007 — `HealthProvider._handle_failure` mutation pattern has no test [medium, S] — `verification.md`
36. TEST-011 — `src/acheron/worker_sdk/cloud.py` `_rp_handler` validation has no direct unit tests [medium, S] — `verification.md`
37. TEST-013 — `src/acheron/worker_sdk/_edge_http.py` multipart helpers have no direct unit tests [medium, S] — `verification.md`
38. TEST-016 — `workers/translategemma/tests/test_handler.py:235-241` class-level mutation anti-pattern [medium, S] — `verification.md`
39. TEST-017 — `tests/integration/test_tls.py` hardcodes 3 repo-relative paths via `Path(__file__).resolve().parents[2]` [medium, S] — `verification.md`
40. DATA-007 — `_runpod_client` `output.artifacts`-not-list path and `FileArtifact` stream edge cases lack direct tests [medium, S] — `verification.md`

## Story counts

| Status | Count |
|---|---|
| open | 107 |
| in-progress | 0 |
| fixed | 3 |
| verified | 69 |
| stale | 1 |
| wontfix | 0 |
| broken-yaml | 3 |

Status deltas vs `eb6849c` (the 8c post-review baseline): verified +18 (Round 1: 15 stories — SEC-008/-009/-011/-018/-020/-021/-022/-023, CORR-014/-026, OBS-004/-010, ARCH-017/-018, MAINT-010, DOC-005; Round 2 B01-B03: 13 stories — SEC-007, CORR-013/-021/-022/-023/-024/-025/-027/-028/-030, DATA-006/-008, ARCH-022); fixed +1 (Round 1: MAINT-016 transitioned to `fixed`); stale +1 (PERF-008 — the `_post_multipart` per-call `httpx.AsyncClient` anti-pattern was deleted by ARCH-022 in B03); open -20. 3 stories have malformed YAML metadata (OBS-007, SEC-011, OBS-009 — status field renders as concatenated strings like `staleopen`); B24 of Round 2 will fix these as 1-line YAML updates. No previously `verified`/`fixed`/`wontfix` stories regressed.

## Changes since last review

The diff `eb6849c..36294f8` (12 commits, 16 files, 858 insertions / 247 deletions) covers Round 1 + Round 2 B01-B03 of code-review-tackle. The substantive code changes are concentrated in `src/acheron/shell/local_handlers.py` (B01: `_validate_source_path` allowlist on `ExtractionHandler` input; new `PathNotAllowedError` in `core/errors.py`), `src/acheron/shell/transports/_multipart.py` (B02: new shared parser helper for `multipart/mixed` with `ParsedPart` dataclass, `X-Acheron-Part-Name: metrics` selector, missing-boundary `WorkerError` raise), `src/acheron/shell/transports/http.py` (B02: extracted parser; B03: deleted `_post_multipart` in favor of `_request("POST", "/execute", files=form)`), `src/acheron/core/models.py` (B02: `OutputFile.metadata: dict[str, JsonValue]` field threaded through materialization), `src/acheron/worker_sdk/_edge_http.py` (B03: widened exception catch in `_run_execute_multipart`; new `_classify_parts`/`_build_input`/`_decode_metadata` helpers; strict `audio/*` + JSON-only part classification), `src/acheron/worker_sdk/cloud.py` (B03: type-check `input_audio` is dict and `content_type` is str in `_rp_handler`). New test files: `tests/shell/transports/test_http_multipart.py` (272 lines, 10 direct unit tests for B02 parser), `tests/worker_sdk/test_cloud_audio.py` (46 lines), `tests/worker_sdk/test_edge_http_multipart.py` (127 lines). Story YAML bumps: 6 stories moved to `verified` in B01 (SEC-007), 6 in B02 (CORR-013, -027, -028, -030, DATA-006, -008), 6 in B03 (CORR-021, -022, -023, -024, -025, ARCH-022). 1 story marked `stale` (PERF-008) — its `_post_multipart` anti-pattern was deleted by ARCH-022. 33 other open stories had their `last_verified_at.commit` and `lines:` updated to post-fix locations across the touched files (`http.py`, `_multipart.py`, `models.py`, `local_handlers.py`, `cloud.py`, `_edge_http.py`).

Dominant themes (this delta):

- **CORR (C → B)** — 4 stories resolved (CORR-013 metadata propagation through `_parse_multipart`; CORR-027 multi-file upstream dispatch; CORR-028 missing-boundary IndexError; CORR-030 metrics-part selector by header). The 8c 11-medium burst (CORR-026, -027, -028, -029, -031, -032, -033) is now down to 7 medium (still above 2 but below the 9-15 C threshold).

- **ARCH (B)** — 1 story resolved (ARCH-022: `_post_multipart` collapsed into `_request` via shared helper, removing the near-byte-duplicate). 12 ARCH stories remain open, all medium or low severity.

- **DATA (B → A)** — 2 stories resolved (DATA-006, DATA-008 — direct unit tests for `_parse_multipart` edge cases). 3 stories remain open (DATA-005, -007, -009, all medium). The theme drops back to A now that the medium count is at the ≤2 threshold.

- **DOC (B → A)** — None resolved in this delta, but the medium count naturally drops to 2 (DOC-005 was verified in Round 1; DOC-003 and DOC-004 remain open). The theme drops back to A.

- **SEC (C → B)** — SEC-007 (the only remaining high in SEC) is verified in B01. All other SEC stories (SEC-005, -006, -010, -012, -013, -014, -015, -016, -017, -019) are now medium or low. The C-grade driver (SEC-008 critical + 2 high + the 8c-added 4 SEC) is gone.

- **MAINT (B)** — 1 story fixed (MAINT-016 in Round 1 — `InvalidLanguagePathError` parent dropped from `ChunkingTooLongForWorkerError`). 1 story marked stale indirectly: PERF-008 in operations.md (B03 ARCH-022 deleted `_post_multipart`, so the per-call `httpx.AsyncClient` anti-pattern it flagged is now solely covered by PERF-007).

- **TEST/EXC/TYPE/REPRO/CFG/PERF/OBS** — unchanged themes (no stories in this delta).

7 themes at A (CORR→B now; ML, MATH, TYPE, REPRO, DATA, DX, PKG, DOC), 8 themes at B (ARCH, CFG, MAINT, EXC, TEST, PERF, OBS, SEC), 0 themes at C. No aggregate codebase grade (per the rubric). The 3 broken-YAML stories (OBS-007, SEC-011, OBS-009) are scheduled for B24 of Round 2; the YAML front matter (e.g. `staleopen`) is malformed but the `last_verified_at`, `files`, and Issue/Why/Recommendation sections are intact.

## Last orientation snapshot

**Repository**: acheron — audiobook processing pipeline (FastAPI orchestrator + gRPC/HTTP workers + Redis/memory stores). Greenfield (per AGENTS.md).

**Branch / HEAD**: `master` at `36294f8` (FF-merged from `fix/code-review-tackle-2` after B03). 12 commits ahead of `origin/master` (not yet pushed).

**Top-level layout**: `src/acheron/core/` (domain models, errors, chunking, planner, interfaces), `src/acheron/shell/` (orchestrator, API, executors: streaming/async/sequential, stores: memory/redis, transports: http/grpc/local, cache, health, TLS, step_handler, local_handlers, capabilities, health_providers, config), `src/acheron/worker_sdk/` (base SDK for building workers — config_loader, _edge_http, _runpod_client, registration, pricing, artifacts, cloud, handler, app, cli, settings, schemas, inputs), `src/acheron/tls.py` (top-level — TLS helpers shared by shell + worker_sdk + workers), `dashboard/` (separate package), `stubs/` (7 generic SDK-backed stubs + _sdk_base + nltk mock), `workers/qwen3tts/` (RunPod serverless TTS worker, uv workspace member), `workers/granite_speech/` (RunPod serverless ASR worker, uv workspace member), `workers/translategemma/` (RunPod serverless translation worker, uv workspace member), `workers/_shared.py` + `workers/_shared/` (shared helpers — `safe_chapter_id`), `tests/` (mirrors src: tests/core, tests/shell, tests/worker_sdk, tests/integration, tests/scripts; plus stubs/tests/, workers/<pkg>/tests/).

**No hexagonal layers**: flat package structure. Interfaces (ABCs) in `core/interfaces.py`. No `ports.py` files.

**Boundaries** (enforced by import-linter): `core` must NOT import `shell`; `worker_sdk` must NOT import `shell`; `workers` must NOT import `shell`. The `src/acheron/tls.py` is at the top level so both `shell` and `worker_sdk`/`workers` can consume it without violating the import-linter contract.

**Test landscape**: `tests/core/`, `tests/shell/{api,stores,transports}`, `tests/worker_sdk/` (18 test files mirroring 14 source modules), `tests/integration/`, `tests/scripts/`. New since the previous review: `tests/shell/transports/test_http_multipart.py` (272 lines, B02), `tests/worker_sdk/test_cloud_audio.py` (46 lines, B03), `tests/worker_sdk/test_edge_http_multipart.py` (127 lines, B03). Workspace root has a `conftest.py` for cross-workspace test discovery.

**Tooling**: `just certs install lint-imports lint-strict proto test type-check type-check-pyright validate`. All deps `~=` pinned. uv workspace members: `workers/{qwen3tts, granite_speech, translategemma, _shared}`. PKG-003 (cryptography pin drift) remains: `Dockerfile:39` pins `cryptography~=49.0` while root `pyproject.toml:168` pins `cryptography~=46.0`; not fixed yet.

**Key entry points**: `acheron.cli:main`, `acheron.worker_sdk.cli:main` (`acheron-worker-edge`), `acheron.shell.api.__main__`, `acheron.shell.api.app:create_app`. Worker runpod entrypoints: `workers/<pkg>/runpod_entrypoint:main`. Worker edge entrypoints: `worker_sdk/cli.py` (configurable via `WORKER_NAME` env var; expects `/app/<name>.worker.yaml`).

**Changes since last review** (delta brief): the diff `eb6849c..36294f8` is 12 commits, 16 files, +858/-247. B01: `ExtractionHandler` gains `_validate_source_path` allowlist + new `PathNotAllowedError(AcheronError)`; 4 path-traversal unit tests in `tests/shell/test_local_handlers.py`. B02: new `src/acheron/shell/transports/_multipart.py` (100 lines) with `ParsedPart` dataclass + `_parse_multipart_parts` helper; `OutputFile.metadata: dict[str, JsonValue]` field added; `HttpWorker._parse_multipart` now delegates to the shared helper; `_execute_with_upstream_input` raises `WorkerError` on multiple matching files. B03: `_post_multipart` deleted; call site uses `_request("POST", "/execute", files=form)`; `_edge_http.py:_run_execute_multipart` catches `(WorkerError, ValueError, KeyError)` and re-raises non-WorkerError as a sanitised `WorkerError`; `_parse_multipart_request` refactored with `_classify_parts`/`_build_input`/`_decode_metadata` helpers; strict `audio/*` + JSON-only part classification with explicit `WorkerError` for unsupported content types; `cloud.py:_rp_handler` type-checks `input_audio` is dict and `content_type` is str. 18 stories verified total (15 from Round 1 + 13 from B01-B03 minus duplicates). 1 story marked stale (PERF-008).

**Hand off to `code-review-tackle` for remaining open stories.** B04-B25 of Round 2 remain on the worktree branch `fix/code-review-tackle-2`. The most impactful remaining items to tackle first (lowest effort, highest severity — all medium now since no high-severity open):
1. CORR-018 (medium, M) — stream the ASR multipart body instead of materializing
2. CORR-019 (medium, M) — stream the SDK edge multipart body instead of materializing
3. CORR-029 (medium, M) — add partial-success handling to `_translate_batch`
4. ARCH-014 (medium, M) — replace `WorkerType.ASR` branch with match-based dispatch
5. ARCH-020 (medium, M) — replace leaky triple-magic-string signature with `StepDispatch` table
6. EXC-001 (medium, M) — wire tenacity retries OR remove the dead dependency and unused exceptions
7. SEC-005 (medium, S) — make worker registration fail-closed when `open_registration` is False
8. SEC-006 (medium, S) — make `/execute` bearer check unconditional
9. B24 will fix the 3 broken-YAML stories (OBS-007, SEC-011, OBS-009) as 1-line YAML updates.
