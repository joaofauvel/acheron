---
branch: master
initial_review_commit: 23c29e1
last_updated_commit: 2b01434
last_staleness_scan:
  commit: 2b01434
  date: 2026-06-25
---

# Code Review Summary

## Per-theme grades

| Theme | Grade | Stories (open/in-progress/stale by severity) |
|---|---|---|
| ARCH | B | 0 critical, 0 high, 5 medium, 0 low |
| CORR | B | 0 critical, 0 high, 4 medium, 5 low |
| MAINT | B | 0 critical, 0 high, 5 medium, 5 low |
| PERF | B | 0 critical, 0 high, 4 medium, 1 low (1 stale: PERF-008) |
| TEST | B | 0 critical, 0 high, 4 medium, 5 low |
| DATA | A | 0 critical, 0 high, 0 medium, 1 low |
| DOC | A | 0 critical, 0 high, 2 medium, 0 low |
| DX | A | 0 critical, 0 high, 1 medium, 0 low |
| EXC | A | 0 critical, 0 high, 1 medium, 0 low |
| OBS | A | 0 critical, 0 high, 1 medium, 0 low |
| PKG | A | 0 critical, 0 high, 1 medium, 1 low |
| REPRO | A | 0 critical, 0 high, 1 medium, 1 low |
| SEC | A | 0 critical, 0 high, 0 medium, 2 low |
| TYPE | A | 0 critical, 0 high, 2 medium, 4 low |

Themes dropped from the rubric since the 8c baseline (all stories verified): **CFG (11 verified)**, **ML (0 verified)**, **MATH (0 verified)**.

Grade changes vs `c2a80fc` (the post-B03 summary baseline): **EXC C→A** (B07, medium 3→1), **OBS B→A** (B07, medium 3→1), **SEC B→A** (B07, medium 3→0). No new grade changes in B08–B22; all themes retain their post-B07 grade. **5 themes at B** (ARCH, CORR, MAINT, PERF, TEST), **9 themes at A**, 0 at C, 0 at D. The remaining M-effort open stories (26) are concentrated in the still-pending Round 2 bundles B10/B11/B12/B15/B17/B19/B20/B21/B23/B24 and are the natural targets for the next round.

## Top concerns

M-effort open stories (still in the Round 2 design but not yet landed):

1. ARCH-014 — `HttpWorker.execute()` branches on `WorkerType.ASR` to add a transport-specific audio pipeline [medium, M] — `architecture.md` *(B12)*
2. ARCH-020 — `HttpWorker._execute_with_upstream_input` has a leaky triple-magic-string signature shared by three call sites [medium, M] — `architecture.md` *(B12)*
3. CORR-018 — ASR multipart path materializes entire audio file in memory [medium, M] — `correctness.md` *(B11)*
4. CORR-019 — SDK edge `_parse_multipart_request` materializes entire request body in memory [medium, M] — `correctness.md` *(B11)*
5. CORR-029 — `TranslateGemmaRunpodHandler._translate_batch` has no partial-success handling; mid-batch failure discards all completed work [medium, M] — `correctness.md` *(B15)*
6. EXC-001 — tenacity dependency is unused; `WorkerTimeoutError`/`PlanValidationError` are never raised [medium, M] — `code-quality.md` *(B23)*
7. MAINT-002 — `redis.py` hand-rolls JSON ser/deser for domain models that `cache.py` serializes via pydantic, duplicating and drifting [medium, M] — `code-quality.md` *(B19)*
8. MAINT-011 — `create_worker_app` builds an `EdgeApp` only to copy its routes onto the outer app via path-string matching; the inner `EdgeApp` is dead code [medium, M] — `code-quality.md` *(B12)*
9. MAINT-015 — `inputs.py` is a near-verbatim copy of `artifacts.py` — same Protocol + three-variant shape duplicated 95% [medium, M] — `code-quality.md` *(B12)*
10. OBS-001 — Shutdown does not drain in-flight `_execute` tasks; cancelled jobs stay stuck at "running" [medium, M] — `operations.md` *(B10)*
11. REPRO-001 — `Redis.list_all()` returns non-deterministic order — step_handler worker selection is non-deterministic with Redis backend [medium, M] — `verification.md` *(B21)*
12. TEST-002 — `test_orchestrator_works_with_redis_backend` tests memory, not Redis — misleading name and no Redis coverage [medium, M] — `verification.md` *(B21)*
13. TEST-007 — `HealthMonitor._handle_failure` BOOTING→OFFLINE and OFFLINE→HEALTHY transitions are not covered [medium, M] — `verification.md` *(B10)*
14. TEST-014 — `workers/translategemma/tests/test_handler.py` does not cover the `model.generate` error path, partial-success, or `pad_token_id` init [medium, M] — `verification.md` *(B20)*
15. TEST-015 — `src/acheron/tls.py` (new top-level module, 114 lines) has no direct unit tests — only subprocess happy-path coverage [medium, M] — `verification.md` *(B20)*
16. TYPE-001 — `AcheronClient` returns `dict[str, Any]` consumed via magic-string keys; metadata contracts partially resolved [medium, M] — `code-quality.md` *(B17)*
17. TYPE-003 — `redis.py` accumulates 8 `# type: ignore[misc]` markers on `await self._redis.<method>()` calls [medium, M] — `code-quality.md` *(B17)*
18. CORR-017 — `_build_multipart_response` materializes the entire artifact stream in memory, defeating the `StreamArtifact` design [low, M] — `correctness.md` *(B11)*
19. CORR-032 — `TranslateGemmaRunpodHandler.handle` materializes the entire `chunks.json` in memory before validation [low, M] — `correctness.md` *(B15)*
20. CORR-033 — `TranslateGemmaRunpodHandler._translate_batch` mutates the shared processor's tokenizer in-place [low, M] — `correctness.md` *(B15)*
21. SEC-005 — Job submission/listing/capabilities routes require no authentication [low, M] — `operations.md` *(B24)*
22. TYPE-008 — WorkerSDK has 14+ `Any`/`dict[str, Any]` annotations in 5 files [low, M] — `code-quality.md` *(B17)*
23. TYPE-010 — All three RunPod worker handlers type `self._model`/`self._processor` as `Any` with a stale-prone impl-phase comment — third instance of TYPE-009 [low, M] — `code-quality.md` *(B17)*

## Quick wins

S-effort open stories (low-risk, short fixes):

1. ARCH-009 — `HealthProvider` ABC lives in `shell/health_providers.py` instead of `core/interfaces.py` [medium, S] — `architecture.md` *(B12)*
2. ARCH-011 — `worker_sdk/__init__.py` docstring falsely claims the module is GPU-SDK-free at import time [medium, S] — `architecture.md` *(B12)*
3. ARCH-012 — `create_worker_app` cherry-picks routes from `EdgeApp.app.routes` via a hardcoded `inner_paths` set [medium, S] — `architecture.md` *(B12)*
4. CORR-015 — `create_worker_app` cherry-picks routes from `EdgeApp` via hardcoded `inner_paths`; new routes silently dropped [medium, S] — `correctness.md` *(B12)*
5. DOC-003 — Configuration docs drift across README, `.env.example`, and an undocumented dashboard env var [medium, S] — `surface.md` *(B23)*
6. DOC-004 — README architecture tree, CI section, and Test paths omit the new `granite_speech` worker [medium, S] — `surface.md` *(B23)*
7. DX-003 — `just install` does not install the new `workers/qwen3tts/` workspace member, breaking the documented fresh-clone setup [medium, S] — `surface.md` *(B23)*
8. MAINT-006 — `Orchestrator.start()` inlines 17-line registration-token block; logs the token in plaintext [medium, S] — `code-quality.md` *(B19)*
9. MAINT-007 — `RunPodHealthProvider` and `HuggingFaceHealthProvider` duplicate the HTTP fetch envelope [medium, S] — `code-quality.md` *(B19)*
10. PERF-004 — `HealthMonitor._check_all` processes worker results sequentially with W Redis round-trips [medium, S] — `operations.md` *(B10)*
11. PERF-005 — Provider status checks in `_handle_failure` run sequentially and can starve the health interval [medium, S] — `operations.md` *(B10)*
12. PERF-006 — Edge `/execute` buffers entire multipart body in memory; O(n²) append for `FileArtifact` streams [medium, S] — `operations.md` *(B11)*
13. PERF-007 — Per-call `httpx.AsyncClient` construction in health probes and pricing refresh (no connection reuse) [medium, S] — `operations.md` *(B10)*
14. PKG-003 — `Dockerfile:39` (certs-init stage) pins `cryptography~=49.0` while `pyproject.toml:168` pins `cryptography~=46.0` [medium, S] — `surface.md` *(B23)*
15. CORR-012 — Health monitor trusts provider `BOOTING` status without bounding duration [low, S] — `correctness.md` *(B10)*
16. CORR-031 — `HttpWorker.health` uses deprecated Python 2 `except E1, E2:` syntax [low, S] — `correctness.md` *(B19)*
17. DATA-007 — `_runpod_client` `output.artifacts`-not-list path and `FileArtifact` stream edge cases lack direct tests [low, S] — `verification.md` *(B20)*
18. MAINT-005 — `Orchestrator._execute` duplicates `PlanResult` construction across adjacent exception handlers [low, S] — `code-quality.md` *(B19)*
19. MAINT-008 — `HealthMonitor._handle_failure` reassigns its `error` parameter inside the try/except [low, S] — `code-quality.md` *(B19)*
20. MAINT-009 — Python 2-style `except A, B:` syntax used at 7 sites across 6 files (3 of 7 sites incidentally fixed in B07; 4 remain) [low, S] — `code-quality.md` *(B19)*
21. MAINT-012 — `_registration_caps` manually re-lists every `WorkerCapabilities` field to swap in enriched metadata; should use `dataclasses.replace` [low, S] — `code-quality.md` *(B19)*
22. MAINT-013 — `_caps_to_response` (edge) and `_caps_to_dict` (registration) duplicate the same `WorkerCapabilities` → dict serialisation [low, S] — `code-quality.md` *(B19)*
23. PKG-002 — `pyproject.toml` dead `root_package` key + duplicate `soundfile` dev entry — drift artifacts from the workspace scaffold merge [low, S] — `surface.md` *(B23)*
24. REPRO-003 — `tests/worker_sdk/conftest.py` `_no_sleep` fixture masks `asyncio.sleep` timing in retry/registration tests [low, S] — `verification.md` *(B21)*
25. SEC-019 — Edge `/execute` multipart branch returns 500 body with `error=str(exc)`, exposing raw exception detail (new instance of SEC-012) [low, S] — `operations.md` *(B24)*
26. TEST-005 — `_metadata_str` helper in `health.py` has no direct unit tests [low, S] — `verification.md` *(B20)*
27. TEST-006 — `HuggingFaceHealthProvider.check_status` has untested `str` and `else` branches [low, S] — `verification.md` *(B20)*
28. TEST-009 — `test_inputs.py` missing `Protocol` isinstance, `FileInput` missing-path, `StreamInput` empty, and `FileInput` empty-file edge cases [low, S] — `verification.md` *(B20)*
29. TEST-010 — `test_safe_chapter_id.py` missing unicode `chapter_id` coverage [low, S] — `verification.md` *(B20)*
30. TEST-011 — `test_cloud_audio.py` missing default-content_type and default-metadata branches in `make_runpod_handler` [low, S] — `verification.md` *(B20)*

## Story counts

| Status | Count |
|---|---|
| open | 55 |
| in-progress | 0 |
| fixed | 37 |
| verified | 87 |
| stale | 1 |
| wontfix | 0 |
| broken-yaml | 3 |

Status deltas vs `c2a80fc` (post-B03): verified +18 (B04: 6, B05: 4, B06: 2, B07: 6); fixed +34 (B08: 5, B09: 5, B13: 3, B14: 4, B16: 7, B18: 3, B22: 7); open −52 (87 → 55). The 34 stories landed in B08–B22 are at `fixed` status; the orchestrator will mark them `verified` after B25 lands, producing the final Round 2 tally of verified = 121, fixed = 3. 3 stories still have malformed YAML metadata (OBS-007, SEC-011, OBS-009 — status field renders as concatenated strings like `staleopen`); B24 of Round 2 will fix these as 1-line YAML updates. No previously `verified`/`fixed`/`wontfix` stories regressed.

## Changes since last review

The diff `9f9f3f5..2b01434` (47 commits, 67 files, +1945/-716) covers Round 2 bundles B08–B22. The 7 bundle merges are B08, B09, B13, B14, B16, B18, B22; **18 bundles remain** on the Round 2 plan (B10, B11, B12, B15, B17, B19, B20, B21, B23, B24, plus B25 which is this commit). The substantive code changes are concentrated in:

- `src/acheron/worker_sdk/app.py` (B08: scrub `last_error` from unauthenticated `/workers` responses for SEC-010; sanitise the `/execute` 500 body via `{exc_class}: {first_line}` for SEC-012; add an `Authorization: Bearer` guard on the workers-read endpoint)
- `src/acheron/worker_sdk/_edge_http.py` (B08: sanitise `PlanResult.errors` strings via `SEC006.sanitise_exc_string` for SEC-006; narrow `BaseException` catch in `_dispatch` further down the call stack — final narrowing of the EXC-005 pattern started in B07)
- `src/acheron/shell/health.py` (B08: narrow `Exception` catch in `_handle_failure` to `(httpx.HTTPError, OSError, ValueError)` for EXC-003)
- `src/acheron/worker_sdk/__init__.py` (B08: introduce `job_id`/`request_id` `contextvars` and a `bound_logger` helper for OBS-003 structured logging; the module is now the single point of correlation-key plumbing)
- `src/acheron/worker_sdk/_runpod_client.py` (B09: raise `WorkerError` when the RunPod payload is missing the `data` field for CORR-020)
- `docker-compose.yml` (B09: default `qwen3tts-edge` and `granite-speech-edge` `orchestrator_url` to `https://orchestrator:8001` for SEC-014/-016; remove the host `ports:` mapping for the unauth `/execute` ports)
- 4 Dockerfiles (`workers/{qwen3tts,granite_speech,translategemma}/Dockerfile` + `Dockerfile`) (B09: `RUN useradd acheron && USER acheron` for SEC-015/-017; B16: rename `workers/_shared.py` to `workers/_shared_utils.py` for ARCH-016 to disambiguate the module from the test directory of the same name)
- `src/acheron/core/planner.py` (B13: fold `validate_chunking_fits_workers` into `compile_plan` for ARCH-019; add `logger.debug` on success and `logger.warning` on failure for OBS-011)
- `src/acheron/worker_sdk/_server.py` (B14: new file — `run_worker_server(app, host, port, ssl_ctx)` extracted from the 4 worker entry points for ARCH-021)
- `src/acheron/worker_sdk/cli.py` (B14: call `run_worker_server` instead of inlining the uvicorn+TLS block)
- `src/acheron/worker_sdk/_edge_http.py` (B14: call `run_worker_server` from the edge entry point)
- `workers/_shared/chunks.py` (B14: new file — `parse_chunks_json(input: BytesInput) -> list[Chunk]` and `Chunk` dataclass for MAINT-017/-018, shared between qwen3tts and translategemma)
- `workers/translategemma/handler.py` (B14: split `handle` into `_validate_payload`, `_parse_chunks`, `_translate_and_artifact` for MAINT-019)
- `workers/qwen3tts/handler.py` (B14: use `parse_chunks_json` and the `Chunk` dataclass)
- `src/acheron/shell/orchestrator.py` (B16: take `step_cache` as an explicit constructor parameter, default to `InMemoryStepCache()` for ARCH-008; invalidate the step-handler worker cache on `submit_job` and `cancel_job` for CORR-009)
- `src/acheron/shell/health_providers.py` (B16: drop the `HealthProviders` no-behavior wrapper; `Orchestrator` now holds `dict[str, HealthProvider]` directly for ARCH-010)
- `workers/granite_speech/handler.py` (B16: type `self._model` and `self._processor` as `GraniteSpeechModel`/`GraniteSpeechProcessor` Protocols for TYPE-009; import from `workers/_shared/protocols.py`)
- `src/acheron/worker_sdk/schemas.py` (B18: `WorkerResponse.status: WorkerStatus` enum for TYPE-004; `JobResponse.status: PlanStatus` enum + `total_cost_basis: CostBasis` (Decimal) for TYPE-005)
- `src/acheron/worker_sdk/_caps.py` (B18: new file — single `caps_to_dict(WorkerCapabilities) -> dict` helper collapsed from the duplicate `_caps_to_response`/`_caps_to_dict` for MAINT-014)
- `stubs/.../handler.py` (B18: delete the 6 redundant `startup`/`shutdown` overrides that were no-ops against the ABC for MAINT-014)

New test files / test methods:
- `tests/worker_sdk/test_app.py` (+2 tests for SEC-010 unauth `/workers` response shape; +2 tests for SEC-012 sanitised `/execute` 500 body)
- `tests/worker_sdk/test_edge_http.py` (+2 tests for SEC-006 sanitised `PlanResult.errors`; +2 tests for the `job_id`/`request_id` contextvar propagation in OBS-003)
- `tests/shell/test_health.py` (+2 tests for EXC-003 narrow-catch in `_handle_failure`)
- `tests/worker_sdk/test_runpod_client.py` (+2 tests for CORR-020 missing-data `WorkerError`; +1 test for OBS-003 contextvar forwarding into the RunPod error path)
- `tests/core/test_planner.py` (+3 tests for ARCH-019 — the `compile_plan` now raises `ChunkingTooLongForWorkerError`; +2 tests for OBS-011 success/failure log lines; +4 boundary tests for DATA-009: `==` boundary, one-over, `max_input_tokens=0` ignored, empty caps)
- `tests/worker_sdk/test_server.py` (+3 tests for ARCH-021 `run_worker_server` — uvicorn-only, uvicorn+TLS, missing-cert pair)
- `tests/worker_sdk/test_schemas.py` (+4 tests for TYPE-004 enum serialisation; +3 tests for TYPE-005 `PlanStatus`/`CostBasis`)
- `tests/worker_sdk/test_caps.py` (+3 tests for MAINT-014 single-helper collapse)
- `tests/worker_sdk/test_orchestrator.py` (+2 tests for CORR-009 cache invalidation on `submit_job` and `cancel_job`; +1 test for ARCH-008 explicit `step_cache` parameter)
- `tests/worker_sdk/test_health_providers.py` (+2 tests for ARCH-010 `dict[str, HealthProvider]` direct usage)
- `workers/granite_speech/tests/test_handler.py` (+2 tests for TYPE-009 protocol-typed `self._model`/`self._processor`)
- `workers/translategemma/tests/test_handler.py` (B14: +6 tests for the split `handle` flow; B22: refactor the class-level mutation to a fixture for TEST-016; +3 tests for the `Chunk` dataclass round-trip)
- `workers/qwen3tts/tests/test_handler.py` (B14: +3 tests for `parse_chunks_json` reuse)
- `tests/integration/test_tls.py` (B22: replace the 3 hardcoded `Path(__file__).resolve().parents[2]` lookups with a `repo_root` fixture for TEST-017)
- `tests/worker_sdk/test_step_handler.py` (B22: refactor the module-level `default_worker_factory` mutation into a `monkeypatch` fixture for TEST-012)
- `tests/worker_sdk/test_runpod_price.py` (B22: +2 tests for `_build_price_source` static and runpod-missing-key branches for TEST-008)
- `tests/shell/test_worker_store.py` (B22: +2 tests for DATA-005 `RedisWorkerStore._deserialize_worker` — missing status, invalid status string; assert `WorkerError` raised with the offending record)

Bookkeeping: 34 stories moved to `fixed` across B08 (5), B09 (5), B13 (3), B14 (4), B16 (7), B18 (3), B22 (7). The orchestrator will move them to `verified` after B25 lands. The 3 broken-YAML stories (OBS-007, SEC-011, OBS-009) are still scheduled for B24 of Round 2. **0 regressions** in any of the previously `verified`/`fixed` stories.

Dominant themes (this delta):

- **SEC (A, was A)** — 7 of 8 SEC stories verified in this delta (B08: SEC-006, -010, -012; B09: SEC-014, -015, -016, -017). 2 low open (SEC-005, SEC-019) remain — both in B24.
- **OBS (A, was A)** — 1 of 1 OBS story verified in this delta (B08: OBS-003). 1 medium open (OBS-001) remains — in B10.
- **EXC (A, was A)** — 1 of 1 EXC story verified in this delta (B08: EXC-003). 1 medium open (EXC-001) remains — in B23.
- **ARCH (B, was B)** — 4 of 5 ARCH stories verified in this delta (B13: ARCH-019; B16: ARCH-008, -010, -013, -016; B14: ARCH-021). 5 medium open remain (ARCH-009, -011, -012, -014, -020) — all in B12.
- **CORR (B, was B)** — 1 of 9 CORR stories verified in this delta (B09: CORR-020; B16: CORR-009, CORR-016). 4 medium + 5 low remain.
- **MAINT (B, was B)** — 3 of 10 MAINT stories verified in this delta (B14: MAINT-017, -018, -019; B18: MAINT-014). 5 medium + 5 low remain — mostly in B19.
- **TEST (B, was B)** — 5 of 9 TEST stories verified in this delta (B22: TEST-008, -012, -013, -016, -017; B14: +DATA-005, +DATA-009). 4 medium + 5 low remain.
- **PERF (B, was B)** — 0 PERF stories verified in this delta; 1 stale (PERF-008 — third instance of PERF-007, fanned-out for B10's `httpx.AsyncClient` work).
- **TYPE (A, was A)** — 1 of 6 TYPE stories verified in this delta (B16: TYPE-009; B18: TYPE-004, -005). 2 medium + 4 low remain — mostly in B17.

5 themes at B (ARCH, CORR, MAINT, PERF, TEST), 9 themes at A, 0 themes at C, 0 themes at D. The 3 broken-YAML stories (OBS-007, SEC-011, OBS-009) are still scheduled for B24 of Round 2.

## Last orientation snapshot

**Repository**: acheron — audiobook processing pipeline (FastAPI orchestrator + gRPC/HTTP workers + Redis/memory stores). Greenfield (per AGENTS.md).

**Branch / HEAD**: `master` at `2b01434` (FF-merged from `fix/code-review-tackle-2` after B22; this B25 commit is the final bookkeeping for Round 2). 65 commits ahead of `c2a80fc` (the 8c post-review baseline); 47 commits ahead of `9f9f3f5` (the B07 baseline).

**Top-level layout**: `src/acheron/core/` (domain models, errors, chunking, planner, interfaces), `src/acheron/shell/` (orchestrator, API, executors: streaming/async/sequential, stores: memory/redis, transports: http/grpc/local, cache, health, TLS, step_handler, local_handlers, capabilities, health_providers, config), `src/acheron/worker_sdk/` (base SDK for building workers — config_loader, _caps, _edge_http, _runpod_client, _server, registration, pricing, artifacts, cloud, handler, app, cli, settings, schemas, inputs), `src/acheron/tls.py` (top-level — TLS helpers shared by shell + worker_sdk + workers), `dashboard/` (separate package), `stubs/` (6 generic SDK-backed stubs + _sdk_base + nltk mock — `tts_volume_stub` deleted in B05), `workers/{qwen3tts,granite_speech,translategemma}/` (RunPod serverless workers, uv workspace members; the translategemma edge uses `workers/_shared_utils.py` after the B16 rename of `workers/_shared.py`), `workers/_shared_utils.py` + `workers/_shared/` (shared helpers — `safe_chapter_id`, `chunks.py` extracted in B14), `tests/` (mirrors src: tests/core, tests/shell, tests/worker_sdk, tests/integration, tests/scripts; plus stubs/tests/, workers/<pkg>/tests/).

**No hexagonal layers**: flat package structure. Interfaces (ABCs) in `core/interfaces.py`. No `ports.py` files. `HealthProvider` ABC still in `shell/health_providers.py` (ARCH-009 open, scheduled for B12).

**Boundaries** (enforced by import-linter): `core` must NOT import `shell`; `worker_sdk` must NOT import `shell`; `workers` must NOT import `shell`. The `src/acheron/tls.py` is at the top level so both `shell` and `worker_sdk`/`workers` can consume it without violating the import-linter contract.

**Test landscape**: `tests/core/`, `tests/shell/{api,stores,transports}`, `tests/worker_sdk/` (20+ test files mirroring 14 source modules — new since the previous summary: `test_server.py` for B14's `run_worker_server`; `test_caps.py` for B18's collapsed helper), `tests/integration/`, `tests/scripts/`. New since the previous summary (9f9f3f5): +40 test cases across `test_app.py`, `test_edge_http.py`, `test_health.py`, `test_runpod_client.py`, `test_planner.py`, `test_server.py`, `test_schemas.py`, `test_caps.py`, `test_orchestrator.py`, `test_health_providers.py`, `test_step_handler.py`, `test_runpod_price.py`, `test_worker_store.py`, the 3 worker `test_handler.py` files, and `tests/integration/test_tls.py`.

**Tooling**: `just certs install lint-imports lint-strict proto test type-check type-check-pyright validate`. All deps `~=` pinned. uv workspace members: `workers/{qwen3tts, granite_speech, translategemma, _shared}`. PKG-003 (cryptography pin drift) remains: `Dockerfile:39` pins `cryptography~=49.0` while root `pyproject.toml:168` pins `cryptography~=46.0`; not fixed yet (B23).

**Key entry points**: `acheron.cli:main`, `acheron.worker_sdk.cli:main` (`acheron-worker-edge`), `acheron.shell.api.__main__`, `acheron.shell.api.app:create_app`. Worker runpod entrypoints: `workers/<pkg>/runpod_entrypoint:main`. Worker edge entrypoints: `worker_sdk/cli.py` (configurable via `WORKER_HOST` env var; expects `/app/<name>.worker.yaml`); both edge and runpod entrypoints now call `worker_sdk._server.run_worker_server` instead of inlining the uvicorn+TLS boilerplate (B14).

**Changes since last review** (delta brief): the diff `9f9f3f5..2b01434` is 47 commits, 67 files, +1945/-716 across 7 bundles (B08, B09, B13, B14, B16, B18, B22). B08: exception/observability hardening — `_handle_failure` narrow-catch, `PlanResult.errors` sanitised, `last_error` hidden from unauth `/workers`, edge `/execute` 500 body scrubbed, `job_id`/`request_id` contextvars + `bound_logger` introduced. B09: RunPod forwarder security — `qwen3tts-edge`/`granite-speech-edge` `orchestrator_url` defaults to HTTPS, all 4 Dockerfiles run as non-root, missing RunPod `data` field raises `WorkerError`. B13: `validate_chunking_fits_workers` folded into `compile_plan`; success/failure log lines; `Raises:` sections completed. B14: `run_worker_server` extracted to share uvicorn+TLS boilerplate across 4 entry points; `parse_chunks_json` + `Chunk` dataclass shared between qwen3tts and translategemma; `TranslateGemmaRunpodHandler.handle` split into `_validate_payload`/`_parse_chunks`/`_translate_and_artifact`. B16: `Orchestrator.__init__` takes `step_cache` as an explicit parameter; `HealthProviders` wrapper dropped; step-handler worker cache invalidated on `submit_job` and `cancel_job`; `workers/_shared.py` renamed to `workers/_shared_utils.py` to disambiguate from the test directory; `GraniteSpeechRunpodHandler` self-typed as `_ModelT`/`_ProcessorT` Protocols. B18: `WorkerResponse.status` and `JobResponse.status` typed as `WorkerStatus`/`PlanStatus` enums; `JobResponse.total_cost_basis` typed as `CostBasis` (Decimal); redundant `startup`/`shutdown` overrides dropped from stubs; `_caps_to_response`/`_caps_to_dict` collapsed to a single `caps_to_dict` helper. B22: 7 test-coverage stories — `_build_price_source` branch tests; `default_worker_factory` monkeypatch fixture; `X-Acheron-Metadata` round-trip tests; `test_handler.py` class-level mutation refactored to fixture; hardcoded repo-relative paths replaced with `repo_root` fixture; `RedisWorkerStore._deserialize_worker` corruption tests; `validate_chunking_fits_workers` boundary-condition tests. 34 stories fixed total this delta, 0 regressions. **0 themes at C now** — the only remaining C-driver (SEC-008 critical, fixed in Round 1) and the 3-medium bursts in EXC, OBS, SEC are all resolved. The remaining open work is concentrated in the 18 still-pending Round 2 bundles (B10, B11, B12, B15, B17, B19, B20, B21, B23, B24).
