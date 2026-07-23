---
branch: code-review-refresh
initial_review_commit: 23c29e1
last_updated_commit: c53da1db44b8f3323191eafd2db6bea5db3b68fc
last_staleness_scan:
  commit: c53da1d
  date: 2026-07-23
---

# Code Review Summary
## Per-theme grades

| Theme | Grade | Stories (open/in-progress/stale by severity) |
|---|---|---|
| ARCH | B | 0 critical, 0 high, 3 medium, 1 low |
| CFG | A | 0 critical, 0 high, 0 medium, 1 low |
| CORR | B | 0 critical, 0 high, 6 medium, 2 low |
| DATA | A | 0 critical, 0 high, 0 medium, 2 low |
| DOC | A | 0 critical, 0 high, 1 medium, 1 low |
| DX | A | 0 critical, 0 high, 0 medium, 0 low |
| EXC | A | 0 critical, 0 high, 0 medium, 0 low |
| MAINT | A | 0 critical, 0 high, 1 medium, 1 low |
| OBS | A | 0 critical, 0 high, 1 medium, 1 low |
| PERF | B | 0 critical, 0 high, 3 medium, 1 low |
| PKG | A | 0 critical, 0 high, 0 medium, 0 low |
| REPRO | A | 0 critical, 0 high, 0 medium, 2 low |
| SEC | A | 0 critical, 0 high, 0 medium, 0 low |
| TEST | B | 0 critical, 0 high, 3 medium, 8 low |
| TYPE | A | 0 critical, 0 high, 1 medium, 2 low |

Themes dropped from the rubric since the 8c baseline (all stories verified): **ML (0 verified)**, **MATH (0 verified)**.

Grade changes vs `77aadcd` (the previous refresh's baseline): **CFG A→A** (1 high from CFG-012 now verified, 1 new low CFG-013), **ARCH A→B** (ARCH-011/012 now stale, 1 new medium ARCH-024), **CORR A→B** (4 new mediums CORR-036/037/038/039 + CORR-015 stale + 1 new low CORR-040), **DOC A→A** (DOC-007 now verified, 1 new low DOC-008), **MAINT A→A** (1 new medium MAINT-021), **PERF A→B** (no grade change but 1 new medium story changes counts), **TEST B→B** (TEST-002/007/015 verified, 2 new mediums TEST-021/022 + 1 new low DATA-010), **TYPE A→A** (TYPE-001/003/006/007/010 verified, 1 new medium TYPE-012), **OBS A→A** (OBS-001 verified, 1 new medium OBS-013). **4 themes at B** (ARCH, CORR, PERF, TEST), **13 themes at A**, 0 at C, 0 at D.

## Historical Top Concerns

High-severity open stories: **none**. The Round 4 work verified both prior-high stories (CORR-035 via `b34ced9`, CFG-012 via `56af1f0`); all other open stories are `medium` or `low`.

Top 10 medium-severity open stories (focus areas for the next tackling round):

1. **CORR-036** — translategemma `_translate_all` partial-success catch only covers `(RuntimeError, ValueError)`; `IndexError`/`KeyError`/`AttributeError`/`MemoryError`/`TypeError`/`CancelledError` bypass and lose partial translations [medium, S] — `correctness.md`
2. **CORR-037** — `Orchestrator._drain_inflight_tasks` docstring claims `asyncio.wait` + `finally` block; the code uses `asyncio.gather` and an `except CancelledError` (no `finally`) [medium, S] — `correctness.md`
3. **CORR-038** — 5s drain timeout can abort mid-shutdown, cancelling the post-cancel `_job_store.put` and leaving the job in RUNNING [medium, S] — `correctness.md`
4. **CORR-039** — `_execute`'s `except Exception` branch logs+re-raises without persisting FAILED, so non-cancel exception paths can still leave jobs in RUNNING [medium, S] — `correctness.md`
5. **ARCH-024** — `api_client.py` imports wire-format response schemas from `shell/api/schemas.py` (server-internal HTTP module) [medium, S] — `architecture.md`
6. **OBS-013** — `Orchestrator._drain_inflight_tasks` is silent on entry/completion/timeout; an unhandled `TimeoutError` can break clean shutdown with no log breadcrumb [medium, S] — `operations.md`
7. **TYPE-012** — `cast("_RedisAwaitable", ...)` at both redis store constructors is an unverified Protocol claim; adding `@runtime_checkable` + `isinstance` would make the typing claim load-bearing at runtime [medium, S] — `code-quality.md`
8. **TEST-021** — `src/acheron/worker_sdk/_io.py` has zero direct unit tests; the Streamable Protocol + 3 stream helpers extracted in MAINT-015 are only covered transitively [medium, S] — `verification.md`
9. **TEST-022** — `redis_container`/`redis_url` fixtures duplicated verbatim between `tests/shell/stores/conftest.py` and `tests/integration/conftest.py` [medium, S] — `verification.md`
10. **MAINT-021** — 4 `except Exception: logger.exception(...); raise` sites in `Orchestrator` duplicate the log+raise pattern; a private `_log_unexpected` helper would centralise the teardown contract [medium, S] — `code-quality.md`

## Quick wins

S-effort open stories (low-risk, short fixes):

1. ARCH-011 — `worker_sdk/__init__.py` docstring falsely claims the module is GPU-SDK-free at import time [medium, S, stale] — `architecture.md`
2. ARCH-012 — `create_worker_app` cherry-picks routes from `EdgeApp.app.routes` via a hardcoded `inner_paths` set [medium, S, stale] — `architecture.md`
3. CORR-015 — `create_worker_app` cherry-picks routes from `EdgeApp` via hardcoded `inner_paths`; new routes silently dropped [medium, S, stale] — `correctness.md`
4. DOC-004 — README architecture tree, CI section, and Test paths omit the new `granite_speech` worker [medium, S, stale] — `surface.md`
5. PERF-004 — `HealthMonitor._check_all` processes worker results sequentially with W Redis round-trips [medium, S] — `operations.md`
6. PERF-005 — Provider status checks in `_handle_failure` run sequentially and can starve the health interval [medium, S] — `operations.md`
7. PERF-007 — Per-call `httpx.AsyncClient` construction in health probes and pricing refresh (no connection reuse) [medium, S] — `operations.md`
8. TEST-021 — `src/acheron/worker_sdk/_io.py` has zero direct unit tests [medium, S] — `verification.md`
9. TEST-022 — `redis_container`/`redis_url` fixtures duplicated verbatim between `tests/shell/stores/conftest.py` and `tests/integration/conftest.py` [medium, S] — `verification.md`
10. OBS-012 — Multipart parse-failure path in `_run_execute_multipart` returns 500 with no `logger.exception` [low, S] — `operations.md`
11. OBS-013 — `Orchestrator._drain_inflight_tasks` is silent on entry/completion/timeout [medium, S] — `operations.md`
12. ARCH-023 — Cross-module import of module-private `_ENV_ONLY_FIELDS` from `worker_sdk/settings.py` to `worker_sdk/config_loader.py` [low, S] — `architecture.md`
13. ARCH-024 — `api_client.py` imports wire-format response schemas from `shell/api/schemas.py` [medium, S] — `architecture.md`
14. CORR-036/037/038/039 — translategemma partial-success + orchestrator drain-handler integrity [medium, S] — `correctness.md`
15. DOC-008 — `src/acheron/worker_sdk/_io.py` re-introduces multi-line docstring shape [low, S] — `surface.md`
16. MAINT-021 — 4 `except Exception: logger.exception(...); raise` sites in `Orchestrator` [medium, S] — `code-quality.md`
17. TYPE-012 — `cast("_RedisAwaitable", ...)` is unverified Protocol claim [medium, S] — `code-quality.md`
18. CFG-013 — 5s drain timeout is hard-coded; no `shutdown_drain_seconds` setting [low, S] — `architecture.md`
19. CORR-012 — Health monitor trusts provider `BOOTING` without bounding duration [low, M] — `correctness.md`
20. CORR-040 — `_execute` CancelledError handler persists FAILED but discards partial PlanResult (under-counts cost) [low, M] — `correctness.md`
21. TEST-005 — `_metadata_str` helper in `health.py` has no direct unit tests [low, S] — `verification.md`
22. TEST-006 — `HuggingFaceHealthProvider.check_status` has untested `str` and `else` branches [low, S] — `verification.md`
23. TEST-009 — `test_inputs.py` missing `Protocol` isinstance, `FileInput` missing-path, `StreamInput` empty, `FileInput` empty-file edge cases [low, S] — `verification.md`
24. TEST-010 — `test_safe_chapter_id.py` missing unicode `chapter_id` coverage [low, S] — `verification.md`
25. TEST-011 — `test_cloud_audio.py` missing default-content_type and default-metadata branches [low, S] — `verification.md`
26. TEST-018 — test_app.py still missing static-without-rate and registration_caps-passthrough tests [low, S] — `verification.md`
27. TEST-019 — TestFileArtifact class is undertested relative to TestBytesArtifact (1 test vs 4) [low, S] — `verification.md`
28. TEST-020 — test_pricing.py has no tests for `ZeroPrice.refresh()` and `StaticPrice.refresh()` [low, S] — `verification.md`
29. DATA-007 — `_runpod_client` output.artifacts-not-list path and FileArtifact stream edge cases lack direct tests [low, S] — `verification.md`
30. DATA-010 — `RedisJobStore._deserialize_job` defensive isinstance branch lacks a parametric test [low, S] — `verification.md`
31. REPRO-003 — `_no_sleep` fixture masks `asyncio.sleep` timing [low, S] — `verification.md`
32. REPRO-004 — `test_orchestrator_works_with_redis_backend` opens its own Redis stores without using the `redis_url` lifespan [low, S] — `verification.md`
33. TYPE-008 — WorkerSDK has 14+ `Any`/`dict[str, Any]` annotations in 5 files [low, M] — `code-quality.md`
34. TYPE-011 — WorkerSDK `Any`/`dict[str, Any]` count is now 25 across 8 files [low, M] — `code-quality.md`

## Generated Bundles

1. **Bundle A — Active-client and shutdown safety:** `CORR-041`, `PERF-009`, `OBS-014` [verified in `7ff0832`, `671c6ad`, `d4ce578`].
2. **Bundle B — Redis and source lifecycle contracts:** `TYPE-013`, `ARCH-025`, `ARCH-026` [verified in `61574e5`, `a50de1a`, `e8732b0`].
3. **Bundle C — Integration and execution coverage:** `TEST-023`, `TEST-024`, `TEST-025`, `TEST-026`, `TEST-027`, `REPRO-005`, `REPRO-006` [verified in `fe63e96`, `298229a`, `2569539`, `0226b03`, `17465be`].
4. **Bundle D — Worker documentation and CI surface:** `DX-005`, `DOC-009`, `DOC-010`, `DOC-011`, `DX-006`, `DOC-012`, `DOC-013` [verified in `55a11ea`].
5. **Bundle E — Worker SDK typing and orchestrator cleanup:** `TYPE-008`, `TYPE-011`, `MAINT-022`, `MAINT-023` [low, M].
6. **Bundle F — Health-state expiry:** `CORR-012` [low, M].

Bundles are ordered by operational risk, then implementation effort. Bundle B is the next tackle target.

## Story counts

| Status | Count |
|---|---|
| open | 5 |
| in-progress | 0 |
| fixed | 61 |
| verified | 159 |
| stale | 8 |
| wontfix | 0 |
| broken-yaml | 0 |

Status deltas vs `77aadcd` (the previous refresh's HEAD): 13 stories moved from `pending` to `verified` (DOC-007, EXC-001, MAINT-011, MAINT-015, TEST-002, TEST-007, TEST-015, REPRO-001, OBS-001, CORR-035, CFG-012, SEC-005, SEC-019) and 3 from `pending` to `verified` for the typed-WorkerCluster (TYPE-001, TYPE-003, TYPE-006, TYPE-007, TYPE-010); 1 marked `stale` (ARCH-012) and 1 stayed `stale` (ARCH-011); 1 previously-`stale` story re-resolved to a new `stale` note (TEST-014 — concern addressed by the TYPE-010 + 299f08c rewrite of translategemma test_handler.py, transition to `fixed` recommended in next tackle pass). 15 new findings filed: ARCH-024, CFG-013, CORR-036, CORR-037, CORR-038, CORR-039, CORR-040, DOC-008, MAINT-021, OBS-013, TEST-021, TEST-022, REPRO-004, DATA-010, TYPE-012 (note: DOC-008 is a regression of the now-verified DOC-007, filed as a new ID per update-mode immutability). 14 line-range re-resolutions applied. Round 4's 12 commits (TYPE-001..010 cleanup, DOC-007 docstring trim, SEC-005/SEC-019 auth fixes, EXC-001 dead-code removal, MAINT-002/011/015 worker-sdk refactors, REPRO-001 sorted Redis list_all, OBS-001 shutdown drain, TEST-002/007/015 test additions) all landed cleanly with 914 tests passing and 93.67% coverage.

## Changes since last review

This refresh covers `77aadcd..59458ba` (31 commits, 71 files): Round 4 B26-B29 tackle work (12 commits) plus 19 supporting commits. Code changes dominated by Type Cleanup (TYPE-001..010), `docstring-line-count` enforcement (DOC-007), worker-sdk refactors (MAINT-002/011/015 → APIRouter + Streamable Protocol + pydantic.TypeAdapter), and the OBS-001 drain + persist fix on shutdown. The post-refresh codebase has 914 tests passing (up from 895); coverage 93.67%.

- 0 high-severity open stories (both prior highs — CORR-035 and CFG-012 — verified in this round).
- 15 new findings filed: 1 ARCH-024, 1 CFG-013, 5 CORR (036-040), 1 DOC-008 (regression of DOC-007), 1 MAINT-021, 1 OBS-013, 4 verification (TEST-021, TEST-022, REPRO-004, DATA-010), 1 TYPE-012.
- 1 staleness flip (ARCH-011 → still stale, line refs updated; ARCH-012 → stale; CORR-015 → stale). TEST-014 stayed stale (cites now-vanished test code; the underlying concern is fully addressed by the 299f08c + e9faa0d rewrites).
- 1 regression filed as a NEW story (DOC-008 re-introduces the multi-line docstring anti-pattern that DOC-007 was supposed to eliminate).
- 14 line-range re-resolutions across the verification, operations, and code-quality bundles.

The next tackling round should focus on: (1) the 5 new CORR findings (036-040) — the OBS-001 fix introduced a partial invariant ('no job is ever left in RUNNING') that the docstring now claims but the code only partially enforces; (2) TEST-021/022 — direct follow-on from MAINT-015 + TEST-002 fixes; (3) TYPE-012 + MAINT-021 — runtime-enforce the `_RedisAwaitable` Protocol and centralise the log+raise pattern in `Orchestrator`; (4) CFG-013 + OBS-013 — make the 5s drain timeout configurable and observable (the OBS-013 silent-drain gap is the operator-facing concern).

## Last orientation snapshot

**Repository**: acheron — audiobook processing pipeline (FastAPI orchestrator + gRPC/HTTP workers + Redis/memory stores). Greenfield (per AGENTS.md).

**Branch / HEAD**: `master` at `59458ba5b1c364bb86ea8390cd30f268b98a6acf`. 31 commits ahead of the previous refresh's HEAD (`77aadcd`); 12 of those are Round 4 (B26-B29) tackle work, the other 19 are supporting commits (TYPE-001..010 typed Pydantic models + Protocol fixes, DOC-007 24-file docstring trim, SEC-005/019 auth fixes, EXC-001 dead-code removal, PKG-002..004 cleanup, DX-003/004 uv workspace install, etc.).

**Top-level layout**: `src/acheron/core/` (domain models, errors, chunking, planner, interfaces), `src/acheron/shell/` (orchestrator, API, executors: streaming/async/sequential, stores: memory/redis, transports: http/grpc/local, cache, health, TLS, step_handler, local_handlers, capabilities, health_providers, config), `src/acheron/worker_sdk/` (base SDK for building workers — config_loader, _caps, _edge_http, _io, _runpod_client, _server, registration, pricing, artifacts, cloud, handler, app, cli, settings, schemas, inputs), `src/acheron/tls.py` (top-level — TLS helpers shared by shell + worker_sdk + workers), `dashboard/` (separate package), `stubs/` (6 generic SDK-backed stubs + _sdk_base + nltk mock), `workers/{qwen3tts,granite_speech,translategemma}/` (RunPod serverless workers, uv workspace members; the translategemma edge uses `workers/_shared_utils.py` after the B16 rename of `workers/_shared.py`), `workers/_shared_utils.py` + `workers/_shared/` (shared helpers — `safe_chapter_id`, `chunks.py` extracted in B14), `tests/` (mirrors src: tests/core, tests/shell, tests/worker_sdk, tests/integration, tests/scripts; plus stubs/tests/, workers/<pkg>/tests/).

**No hexagonal layers**: flat package structure. Interfaces (ABCs) in `core/interfaces.py`. No `ports.py` files. `HealthProvider` ABC lives in `core/interfaces.py` (ARCH-009 verified in B12).

**Boundaries** (enforced by import-linter): `core` must NOT import `shell`; `worker_sdk` must NOT import `shell`; `workers` must NOT import `shell`. The `src/acheron/tls.py` is at the top level so both `shell` and `worker_sdk`/`workers` can consume it without violating the import-linter contract.

**Test landscape**: `tests/core/`, `tests/shell/{api,stores,transports,stubs}`, `tests/worker_sdk/` (15+ test files mirroring 14 source modules — new since the previous summary: `test_io.py` is a TODO from TEST-021; the others are stable), `tests/integration/` (now includes `redis_container`/`redis_url` fixtures duplicated from `tests/shell/stores/conftest.py` — see TEST-022), `tests/scripts/`, `tests/test_tls.py` (top-level, mirrors `src/acheron/tls.py`). 914 tests pass; 93.67% coverage. New since `77aadcd`: +19 tests across TEST-002 (Redis-backed orchestrator), TEST-007 (BOOTING→OFFLINE + OFFLINE→HEALTHY), TEST-015 (13 tls.py unit tests), and various incidental additions in the orchestrator and health-monitor test files.

**Tooling**: `just certs install lint-imports lint-strict proto test type-check type-check-pyright validate`. All deps `~=` pinned. uv workspace members: `workers/{qwen3tts, granite_speech, translategemma, _shared}`. PKG-003 was verified in this round (commit `6ca8fd1` aligned `Dockerfile` and `pyproject.toml` on `cryptography~=`).

**Key entry points**: `acheron.cli:main`, `acheron.worker_sdk.cli:main` (`acheron-worker-edge`), `acheron.shell.api.__main__`, `acheron.shell.api.app:create_app`. Worker runpod entrypoints: `workers/<pkg>/runpod_entrypoint:main`. Worker edge entrypoints: `worker_sdk/cli.py` (configurable via `WORKER_HOST` env var; expects `/app/<name>.worker.yaml`); both edge and runpod entrypoints call `worker_sdk._server.run_worker_server` instead of inlining the uvicorn+TLS boilerplate (B14).

**Changes since last review (delta)**: Round 4 type-cleanup pass (TYPE-001..010) typed AcheronClient to return Pydantic models, replaced the worker `self._model`/`self._processor` `Any` annotations with structural Protocols, and dropped 18+ `# type: ignore` markers. Worker SDK was refactored: MAINT-002 used `pydantic.TypeAdapter` for `WorkerCapabilities` + worker `metadata` ser/de in `redis.py` (partial — outer `TrackedJob`/`Plan`/`PlanResult`/`OutputFile` still hand-rolled); MAINT-011 replaced the `inner_paths` hardcoded whitelist with `APIRouter` via `app.include_router(inner.router)`; MAINT-015 extracted the duplicated `stream()` loop into a shared `Streamable` Protocol + `stream_bytes`/`stream_producer`/`stream_file` helpers in the new `src/acheron/worker_sdk/_io.py`. RESILIENCE: OBS-001 added `_drain_inflight_tasks` to `Orchestrator.shutdown()` and a `try/except CancelledError` arm in `_execute` that persists FAILED status; REPRO-001 sorted `RedisWorkerStore.list_all()` and `RedisJobStore.list_all()` for deterministic step_handler worker selection. SECURITY: SEC-005 gated mutating job routes on `RegistrationTokenDep`; SEC-019 sanitised the `/execute` multipart 500 body. DEPS: dropped `tenacity` (EXC-001) and `soundfile` dev entry (PKG-002); aligned `cryptography~=` across Dockerfile + pyproject (PKG-003); removed the worker `pythonpath` hack (PKG-004); `just install` and `.envrc` use `uv sync --all-packages` (DX-003/004). DOCS: DOC-007 trimmed 24 multi-line module docstrings to single-line summaries. New file: `src/acheron/worker_sdk/_io.py`; new top-level test: `tests/test_tls.py`. New fixture: `tests/integration/conftest.py::redis_container`/`redis_url` (TEST-002). Test count: 895 → 914 (+19). Net LoC: +666 / -204.
