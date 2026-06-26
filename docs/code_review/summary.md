---
branch: master
initial_review_commit: 23c29e1
last_updated_commit: a7aaf1e
last_staleness_scan:
  commit: a7aaf1e
  date: 2026-06-25
---

# Code Review Summary

## Per-theme grades

| Theme | Grade | Stories (open/in-progress/stale by severity) |
|---|---|---|
| ARCH | B | 0 critical, 0 high, 2 medium, 0 low |
| CORR | B | 0 critical, 0 high, 2 medium, 3 low |
| MAINT | B | 0 critical, 0 high, 3 medium, 0 low |
| PERF | B | 0 critical, 0 high, 3 medium, 0 low (1 stale: PERF-008) |
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

Grade changes vs `c2a80fc` (the post-B03 summary baseline): **EXC C→A** (B07, medium 3→1), **OBS B→A** (B07, medium 3→1), **SEC B→A** (B07, medium 3→0). No new grade changes in B11/B12/B19; all themes retain their post-B07 grade. **5 themes at B** (ARCH, CORR, MAINT, PERF, TEST), **9 themes at A**, 0 at C, 0 at D. The remaining M-effort open stories (21) are concentrated in the still-pending Round 2 bundles B10/B12 (partial)/B15/B17/B19 (partial)/B20/B21/B23/B24.

## Top concerns

M-effort open stories (still in the Round 2 design but not yet landed):

1. CORR-029 — `TranslateGemmaRunpodHandler._translate_batch` has no partial-success handling; mid-batch failure discards all completed work [medium, M] — `correctness.md` *(B15)*
2. EXC-001 — tenacity dependency is unused; `WorkerTimeoutError`/`PlanValidationError` are never raised [medium, M] — `code-quality.md` *(B23)*
3. MAINT-002 — `redis.py` hand-rolls JSON ser/deser for domain models that `cache.py` serializes via pydantic, duplicating and drifting [medium, M] — `code-quality.md` *(B19 — deferred)*
4. MAINT-011 — `create_worker_app` builds an `EdgeApp` only to copy its routes onto the outer app via path-string matching; the inner `EdgeApp` is dead code [medium, M] — `code-quality.md` *(B12)*
5. MAINT-015 — `inputs.py` is a near-verbatim copy of `artifacts.py` — same Protocol + three-variant shape duplicated 95% [medium, M] — `code-quality.md` *(B12)*
6. OBS-001 — Shutdown does not drain in-flight `_execute` tasks; cancelled jobs stay stuck at "running" [medium, M] — `operations.md` *(B10)*
7. REPRO-001 — `Redis.list_all()` returns non-deterministic order — step_handler worker selection is non-deterministic with Redis backend [medium, M] — `verification.md` *(B21)*
8. TEST-002 — `test_orchestrator_works_with_redis_backend` tests memory, not Redis — misleading name and no Redis coverage [medium, M] — `verification.md` *(B21)*
9. TEST-007 — `HealthMonitor._handle_failure` BOOTING→OFFLINE and OFFLINE→HEALTHY transitions are not covered [medium, M] — `verification.md` *(B10)*
10. TEST-014 — `workers/translategemma/tests/test_handler.py` does not cover the `model.generate` error path, partial-success, or `pad_token_id` init [medium, M] — `verification.md` *(B20)*
11. TEST-015 — `src/acheron/tls.py` (new top-level module, 114 lines) has no direct unit tests — only subprocess happy-path coverage [medium, M] — `verification.md` *(B20)*
12. TYPE-001 — `AcheronClient` returns `dict[str, Any]` consumed via magic-string keys; metadata contracts partially resolved [medium, M] — `code-quality.md` *(B17)*
13. TYPE-003 — `redis.py` accumulates 8 `# type: ignore[misc]` markers on `await self._redis.<method>()` calls [medium, M] — `code-quality.md` *(B17)*
14. CORR-012 — Health monitor trusts provider `BOOTING` status without bounding duration — step handler treats BOOTING as always-healthy [low, M] — `correctness.md` *(B10)*
15. CORR-032 — `TranslateGemmaRunpodHandler.handle` materializes the entire `chunks.json` in memory before validation [low, M] — `correctness.md` *(B15)*
16. CORR-033 — `TranslateGemmaRunpodHandler._translate_batch` mutates the shared processor's tokenizer in-place [low, M] — `correctness.md` *(B15)*
17. SEC-005 — Job submission/listing/capabilities routes require no authentication [low, M] — `operations.md` *(B24)*
18. TYPE-006 — `grpc.py` accumulates 5 `# type: ignore[...]` markers for the new proto `Artifact` oneof; needs a local `.pyi` stub [low, M] — `code-quality.md` *(B17)*
19. TYPE-007 — `RunPodForwarderHandler.__init__` calls `phantom_handler(settings)` under `# type: ignore[call-arg]`; needs a typed `RunPodHandlerProtocol` factory return [low, M] — `code-quality.md` *(B17)*
20. TYPE-008 — WorkerSDK has 14+ `Any`/`dict[str, Any]` annotations in 5 files [low, M] — `code-quality.md` *(B17)*
21. TYPE-010 — All three RunPod worker handlers type `self._model`/`self._processor` as `Any` with a stale-prone impl-phase comment — third instance of TYPE-009 [low, M] — `code-quality.md` *(B17)*

## Quick wins

S-effort open stories (low-risk, short fixes):

1. ARCH-011 — `worker_sdk/__init__.py` docstring falsely claims the module is GPU-SDK-free at import time [medium, S] — `architecture.md` *(B12)*
2. ARCH-012 — `create_worker_app` cherry-picks routes from `EdgeApp.app.routes` via a hardcoded `inner_paths` set [medium, S] — `architecture.md` *(B12)*
3. CORR-015 — `create_worker_app` cherry-picks routes from `EdgeApp` via hardcoded `inner_paths`; new routes silently dropped [medium, S] — `correctness.md` *(B12)*
4. DOC-003 — Configuration docs drift across README, `.env.example`, and an undocumented dashboard env var [medium, S] — `surface.md` *(B23)*
5. DOC-004 — README architecture tree, CI section, and Test paths omit the new `granite_speech` worker [medium, S] — `surface.md` *(B23)*
6. DX-003 — `just install` does not install the new `workers/qwen3tts/` workspace member, breaking the documented fresh-clone setup [medium, S] — `surface.md` *(B23)*
7. PERF-004 — `HealthMonitor._check_all` processes worker results sequentially with W Redis round-trips [medium, S] — `operations.md` *(B10)*
8. PERF-005 — Provider status checks in `_handle_failure` run sequentially and can starve the health interval [medium, S] — `operations.md` *(B10)*
9. PERF-007 — Per-call `httpx.AsyncClient` construction in health probes and pricing refresh (no connection reuse) [medium, S] — `operations.md` *(B10)*
10. PKG-002 — `pyproject.toml` dead `root_package` key + duplicate `soundfile` dev entry — drift artifacts from the workspace scaffold merge [low, S] — `surface.md` *(B23)*
11. PKG-003 — `Dockerfile:39` (certs-init stage) pins `cryptography~=49.0` while `pyproject.toml:168` pins `cryptography~=46.0` [low, S] — `surface.md` *(B23)*
12. REPRO-003 — `tests/worker_sdk/conftest.py` `_no_sleep` fixture masks `asyncio.sleep` timing in retry/registration tests [low, S] — `verification.md` *(B21)*
13. SEC-019 — Edge `/execute` multipart branch returns 500 body with `error=str(exc)`, exposing raw exception detail (new instance of SEC-012) [low, S] — `operations.md` *(B24)*
14. DATA-007 — `_runpod_client` `output.artifacts`-not-list path and `FileArtifact` stream edge cases lack direct tests [low, S] — `verification.md` *(B20)*
15. TEST-005 — `_metadata_str` helper in `health.py` has no direct unit tests [low, S] — `verification.md` *(B20)*
16. TEST-006 — `HuggingFaceHealthProvider.check_status` has untested `str` and `else` branches [low, S] — `verification.md` *(B20)*
17. TEST-009 — `test_inputs.py` missing `Protocol` isinstance, `FileInput` missing-path, `StreamInput` empty, and `FileInput` empty-file edge cases [low, S] — `verification.md` *(B20)*
18. TEST-010 — `test_safe_chapter_id.py` missing unicode `chapter_id` coverage [low, S] — `verification.md` *(B20)*
19. TEST-011 — `test_cloud_audio.py` missing default-content_type and default-metadata branches in `make_runpod_handler` [low, S] — `verification.md` *(B20)*

## Story counts

| Status | Count |
|---|---|
| open | 40 |
| in-progress | 0 |
| fixed | 52 |
| verified | 87 |
| stale | 1 |
| wontfix | 0 |
| broken-yaml | 3 |

Status deltas vs `c2a80fc` (post-B03): verified +18 (B04: 6, B05: 4, B06: 2, B07: 6); fixed +34 (B08: 5, B09: 5, B13: 3, B14: 4, B16: 7, B18: 3, B22: 7); open −52 (87 → 55 → 40 after B11/B12/B19). B25 promoted the B08–B22 `fixed` stories to `verified` (+34 to verified → 87, 3 left in fixed state for 1-line YAML bookkeeping). B11/B12/B19 added 15 more `fixed` stories (-15 open, +15 fixed) without yet promoting them. The 3 broken-YAML stories (OBS-007, SEC-011, OBS-009 — `status:` field is missing the `status:` key, so they render as concatenated strings like `staleopen`) remain; B24 of Round 2 will fix them. No previously `verified`/`fixed`/`wontfix` stories regressed.

## Changes since last review

The diff `2b01434..a7aaf1e` (15 commits, 34 files, +1041/-545) lands Round 2 bundles **B11, B12, B19** plus a master-fix commit. The 3 bundle merges (B11: 3 commits, B12: 6 commits, B19: 5 commits) plus 1 master-fix commit resolve **15 code-review stories** (4 M-effort, 11 S-effort). The adversarial subagent pass on each branch caught 1 critical reversion in B19 (commit `ab45234` silently reverted `a2f47d9`'s except-tuple fix and moved `health_monitor.start()` into the registration-token helper, so the monitor only ran on the fresh-token-mint path; both fixed in `c3e1bb8`) and 1 partial fix in B12 (MAINT-013 left `_caps_to_response` as a 2-line pass-through wrapper; dropped in `712ae19`). 1 integration-test regression on master (uvicorn 0.49 removed `Server.servers`; conftest now binds a socket first to learn the random port and hands it back via `serve(sockets=[...])`) and 58 proto-file ruff false-positives (`extend-exclude` in `pyproject.toml`) also fixed.

**B11 — HTTP memory materialization (4 stories, 3 commits).** The orchestrator ASR path now streams the audio file via `aiofiles` 64 KiB chunks (`_stream_multipart_request` in `transports/http.py`) and the edge `/execute` response is a `StreamingResponse` instead of a pre-joined `bytes` body. The edge `/execute` request body is consumed via `request.stream()` and parsed chunk-by-chunk through `python-multipart.MultipartParser` low-level callbacks (`_MultipartStreamState` in `_edge_http.py`) — the per-part `X-Acheron-Metadata` header is still captured (no CORR-024 regression). Adds `python-multipart~=0.0` to deps.

- `src/acheron/shell/transports/http.py` — `_stream_multipart_request()` helper; new test `test_asr_multipart_streams_audio_file` monkeypatches `Path.read_bytes` and asserts it is never called (CORR-018).
- `src/acheron/worker_sdk/_edge_http.py` — `_build_multipart_response` returns `StreamingResponse`; `_parse_multipart_request` switched from `BytesParser` + `await request.body()` to `MultipartParser` + `async for chunk in request.stream()`; new tests `test_build_multipart_response_returns_streaming_response`, `test_build_multipart_response_does_not_artifact_append`, `test_parse_multipart_streams_request_body`, `test_parse_multipart_handles_large_file_via_disk_spool` (CORR-017, PERF-006, CORR-019).

**B12 — Worker SDK consolidation (4 stories, 6 commits).** `HealthProvider` ABC moved from `shell/health_providers.py` to `core/interfaces.py` (ARCH-009); `_caps_to_response`/`_caps_to_dict` collapsed to a single `caps_to_dict` in a new `worker_sdk/_caps.py` (MAINT-013 — wrapper dropped in a follow-up commit after subagent review); `HttpWorker.execute()` and `_execute_with_upstream_input` now dispatch via a `StepDispatch` dataclass + `MATCHES_BY_TYPE` table keyed by `WorkerType` (ARCH-020; ARCH-014 marked fixed by the same refactor). The B11 streaming fix and the B12 dispatch table are now composed: `_execute_with_upstream_input` calls `_stream_multipart_request` with `dispatch.form_field` (rebased conflict resolved in `aa10e15`).

- `src/acheron/core/interfaces.py` — `HealthProvider(ABC)` added alongside `Worker` and `Executor`.
- `src/acheron/worker_sdk/_caps.py` — new file; `caps_to_dict(WorkerCapabilities) -> dict[str, Any]`.
- `src/acheron/worker_sdk/registration.py` — drops local `_caps_to_dict`; imports `caps_to_dict` from `_caps`.
- `src/acheron/worker_sdk/_edge_http.py` — `_caps_to_response` removed; `get_capabilities` calls `caps_to_dict` directly.
- `src/acheron/shell/transports/http.py` — `StepDispatch` dataclass + `MATCHES_BY_TYPE` table; `execute()` does one `MATCHES_BY_TYPE.get(job.job_type)` lookup; helper signature is now `(self, job, dispatch)`.
- `src/acheron/shell/health_providers.py`, `src/acheron/shell/health.py`, `tests/shell/test_health_monitor.py` — updated imports to `core.interfaces.HealthProvider`.

**B19 — MAINT cleanup & Python 2 syntax (7 stories, 5 commits).** 4 remaining `except A, B:` sites re-parenthesised (MAINT-009 + CORR-031 — note: the intermediate `chore` commit `ab45234` had accidentally reverted these, the re-apply lives in `c3e1bb8`); `_record_failure` extracted from `Orchestrator._execute`'s adjacent except handlers (MAINT-005); `_handle_failure`'s `error` parameter is now read-only — the chained provider-error message is built into a local `message` (MAINT-008); `_registration_caps` is a `dataclasses.replace(caps, metadata=enriched)` one-liner (MAINT-012); `_load_or_create_registration_token` extracted from `Orchestrator.start` — only the file path is logged, never the token value (MAINT-006); `_fetch_provider_response` extracted so the two `HealthProvider` implementations share one `AsyncClient` lifecycle and `(httpx.HTTPError, OSError) -> None` translation (MAINT-007). The MAINT-006 commit also moved `self._health_monitor.start()` into the helper, which the subagent flagged as a regression (monitor only started on the fresh-token-mint path); fixed in `c3e1bb8` by moving the call back to the end of `start()`.

- `src/acheron/shell/{executors/streaming.py,local_handlers.py,cache.py,transports/http.py}` — `except (X, Y):` form across 4 sites.
- `src/acheron/shell/orchestrator.py` — `_record_failure(tracked, exc)` helper; `_load_or_create_registration_token` helper.
- `src/acheron/shell/health.py` — `message = error`; the provider-chained-error is built into a fresh `message` not the parameter.
- `src/acheron/shell/health_providers.py` — `_fetch_provider_response(provider_name, url, *, headers, timeout)` helper; `RunPodHealthProvider` and `HuggingFaceHealthProvider` now delegate to it.
- `src/acheron/worker_sdk/app.py` — `_registration_caps` is a 2-liner; `WorkerCapabilities` import moved into `TYPE_CHECKING`.

**Master fix (1 commit, `01d44f7`).** `tests/integration/conftest.py` — uvicorn 0.49 only populates `Server.servers` when `serve(sockets=[...])` is called, so the test bound a random port and handed the socket to uvicorn. `pyproject.toml` — added `extend-exclude = ["src/acheron/proto"]` to ruff, mirroring the mypy and basedpyright excludes; the auto-generated `synthesis_pb2.py` triggered 58 false-positive errors (ERA/ISC/PLR/Q/SLF/UP) after the ruff 0.15 bump.

New test methods (B11/B12/B19):
- `tests/shell/transports/test_asr_multipart.py` — `test_asr_multipart_streams_audio_file` (CORR-018).
- `tests/worker_sdk/test_edge_http_multipart.py` — `TestMultipartResponseStreaming` class with 2 tests (CORR-017, PERF-006); `TestParseMultipartRequestStreaming` class with 2 tests (CORR-019).
- `tests/shell/test_health_monitor.py` — existing tests updated to import `HealthProvider` from `core.interfaces` (ARCH-009); no new tests added.

Dominant themes (this delta):

- **CORR (B, was B)** — 4 of 9 CORR stories fixed in this delta (B11: CORR-017, -018, -019, plus CORR-031 in B19). 2 medium + 3 low open remain.
- **MAINT (B, was B)** — 6 of 10 MAINT stories fixed in this delta (B19: MAINT-005, -006, -007, -008, -009, -012, plus MAINT-013 in B12). 3 medium open remain; all low-effort MAINT items are now closed.
- **ARCH (B, was B)** — 3 of 5 ARCH stories fixed in this delta (B12: ARCH-009, -014, -020). 2 medium open remain (ARCH-011 docstring, ARCH-012 app.mount).
- **PERF (B, was B)** — 1 of 4 PERF stories fixed in this delta (B11: PERF-006). 3 medium open remain; PERF-008 still stale.
- **TEST (B, was B)** — 0 TEST stories fixed; unchanged.
- **TYPE (A, was A), SEC (A, was A), OBS (A, was A), EXC (A, was A), DATA (A, was A), DOC (A, was A), DX (A, was A), PKG (A, was A), REPRO (A, was A)** — unchanged.

5 themes at B (ARCH, CORR, MAINT, PERF, TEST), 9 themes at A, 0 themes at C, 0 themes at D. **0 regressions** in any of the previously `verified`/`fixed` stories.

## Last orientation snapshot

**Repository**: acheron — audiobook processing pipeline (FastAPI orchestrator + gRPC/HTTP workers + Redis/memory stores). Greenfield (per AGENTS.md).

**Branch / HEAD**: `master` at `a7aaf1e` (FF-merged from `round2/b11`, `round2/b12`, `round2/b19` after B11/B12/B19 landed). 80 commits ahead of `c2a80fc` (the 8c post-review baseline); 62 commits ahead of `2b01434` (the B25 baseline).

**Top-level layout**: `src/acheron/core/` (domain models, errors, chunking, planner, interfaces), `src/acheron/shell/` (orchestrator, API, executors: streaming/async/sequential, stores: memory/redis, transports: http/grpc/local, cache, health, TLS, step_handler, local_handlers, capabilities, health_providers, config), `src/acheron/worker_sdk/` (base SDK for building workers — config_loader, _caps, _edge_http, _runpod_client, _server, registration, pricing, artifacts, cloud, handler, app, cli, settings, schemas, inputs), `src/acheron/tls.py` (top-level — TLS helpers shared by shell + worker_sdk + workers), `dashboard/` (separate package), `stubs/` (6 generic SDK-backed stubs + _sdk_base + nltk mock — `tts_volume_stub` deleted in B05), `workers/{qwen3tts,granite_speech,translategemma}/` (RunPod serverless workers, uv workspace members; the translategemma edge uses `workers/_shared_utils.py` after the B16 rename of `workers/_shared.py`), `workers/_shared_utils.py` + `workers/_shared/` (shared helpers — `safe_chapter_id`, `chunks.py` extracted in B14), `tests/` (mirrors src: tests/core, tests/shell, tests/worker_sdk, tests/integration, tests/scripts; plus stubs/tests/, workers/<pkg>/tests/).

**No hexagonal layers**: flat package structure. Interfaces (ABCs) in `core/interfaces.py`. No `ports.py` files. `HealthProvider` ABC still in `shell/health_providers.py` (ARCH-009 open, scheduled for B12).

**Boundaries** (enforced by import-linter): `core` must NOT import `shell`; `worker_sdk` must NOT import `shell`; `workers` must NOT import `shell`. The `src/acheron/tls.py` is at the top level so both `shell` and `worker_sdk`/`workers` can consume it without violating the import-linter contract.

**Test landscape**: `tests/core/`, `tests/shell/{api,stores,transports}`, `tests/worker_sdk/` (20+ test files mirroring 14 source modules — new since the previous summary: `test_server.py` for B14's `run_worker_server`; `test_caps.py` for B18's collapsed helper), `tests/integration/`, `tests/scripts/`. New since the previous summary (9f9f3f5): +40 test cases across `test_app.py`, `test_edge_http.py`, `test_health.py`, `test_runpod_client.py`, `test_planner.py`, `test_server.py`, `test_schemas.py`, `test_caps.py`, `test_orchestrator.py`, `test_health_providers.py`, `test_step_handler.py`, `test_runpod_price.py`, `test_worker_store.py`, the 3 worker `test_handler.py` files, and `tests/integration/test_tls.py`.

**Tooling**: `just certs install lint-imports lint-strict proto test type-check type-check-pyright validate`. All deps `~=` pinned. uv workspace members: `workers/{qwen3tts, granite_speech, translategemma, _shared}`. PKG-003 (cryptography pin drift) remains: `Dockerfile:39` pins `cryptography~=49.0` while root `pyproject.toml:168` pins `cryptography~=46.0`; not fixed yet (B23).

**Key entry points**: `acheron.cli:main`, `acheron.worker_sdk.cli:main` (`acheron-worker-edge`), `acheron.shell.api.__main__`, `acheron.shell.api.app:create_app`. Worker runpod entrypoints: `workers/<pkg>/runpod_entrypoint:main`. Worker edge entrypoints: `worker_sdk/cli.py` (configurable via `WORKER_HOST` env var; expects `/app/<name>.worker.yaml`); both edge and runpod entrypoints now call `worker_sdk._server.run_worker_server` instead of inlining the uvicorn+TLS boilerplate (B14).

**Changes since last review** (delta brief): the diff `9f9f3f5..2b01434` is 47 commits, 67 files, +1945/-716 across 7 bundles (B08, B09, B13, B14, B16, B18, B22). B08: exception/observability hardening — `_handle_failure` narrow-catch, `PlanResult.errors` sanitised, `last_error` hidden from unauth `/workers`, edge `/execute` 500 body scrubbed, `job_id`/`request_id` contextvars + `bound_logger` introduced. B09: RunPod forwarder security — `qwen3tts-edge`/`granite-speech-edge` `orchestrator_url` defaults to HTTPS, all 4 Dockerfiles run as non-root, missing RunPod `data` field raises `WorkerError`. B13: `validate_chunking_fits_workers` folded into `compile_plan`; success/failure log lines; `Raises:` sections completed. B14: `run_worker_server` extracted to share uvicorn+TLS boilerplate across 4 entry points; `parse_chunks_json` + `Chunk` dataclass shared between qwen3tts and translategemma; `TranslateGemmaRunpodHandler.handle` split into `_validate_payload`/`_parse_chunks`/`_translate_and_artifact`. B16: `Orchestrator.__init__` takes `step_cache` as an explicit parameter; `HealthProviders` wrapper dropped; step-handler worker cache invalidated on `submit_job` and `cancel_job`; `workers/_shared.py` renamed to `workers/_shared_utils.py` to disambiguate from the test directory; `GraniteSpeechRunpodHandler` self-typed as `_ModelT`/`_ProcessorT` Protocols. B18: `WorkerResponse.status` and `JobResponse.status` typed as `WorkerStatus`/`PlanStatus` enums; `JobResponse.total_cost_basis` typed as `CostBasis` (Decimal); redundant `startup`/`shutdown` overrides dropped from stubs; `_caps_to_response`/`_caps_to_dict` collapsed to a single `caps_to_dict` helper. B22: 7 test-coverage stories — `_build_price_source` branch tests; `default_worker_factory` monkeypatch fixture; `X-Acheron-Metadata` round-trip tests; `test_handler.py` class-level mutation refactored to fixture; hardcoded repo-relative paths replaced with `repo_root` fixture; `RedisWorkerStore._deserialize_worker` corruption tests; `validate_chunking_fits_workers` boundary-condition tests. 34 stories fixed total this delta, 0 regressions. **0 themes at C now** — the only remaining C-driver (SEC-008 critical, fixed in Round 1) and the 3-medium bursts in EXC, OBS, SEC are all resolved. The remaining open work is concentrated in the 18 still-pending Round 2 bundles (B10, B11, B12, B15, B17, B19, B20, B21, B23, B24).
