---
branch: code-review-refresh
initial_review_commit: 23c29e1
last_updated_commit: 77aadcd327643367129d4b3874a3c9c217b40084
last_staleness_scan:
  commit: 77aadcd327643367129d4b3874a3c9c217b40084
  date: 2026-06-26
---

# Code Review Summary

## Per-theme grades

| Theme | Grade | Stories (open/in-progress/stale by severity) |
|---|---|---|
| ARCH | A | 0 critical, 0 high, 2 medium, 1 low |
| CFG | A | 0 critical, 1 high, 0 medium, 0 low |
| CORR | B | 0 critical, 1 high, 3 medium, 3 low |
| DATA | A | 0 critical, 0 high, 0 medium, 1 low |
| DOC | B | 0 critical, 0 high, 3 medium, 0 low |
| DX | A | 0 critical, 0 high, 2 medium, 0 low |
| EXC | A | 0 critical, 0 high, 1 medium, 0 low |
| MAINT | B | 0 critical, 0 high, 3 medium, 1 low |
| OBS | A | 0 critical, 0 high, 1 medium, 1 low |
| PERF | B | 0 critical, 0 high, 3 medium, 1 low |
| PKG | A | 0 critical, 0 high, 1 medium, 2 low |
| REPRO | A | 0 critical, 0 high, 1 medium, 1 low |
| SEC | A | 0 critical, 0 high, 0 medium, 2 low |
| TEST | B | 0 critical, 0 high, 4 medium, 8 low |
| TYPE | A | 0 critical, 0 high, 2 medium, 5 low |

Themes dropped from the rubric since the 8c baseline (all stories verified): **CFG (11 verified)**, **ML (0 verified)**, **MATH (0 verified)**.

Grade changes vs `a7aaf1e` (the post-B11/B12/B19 summary baseline): **CFG A→A** (still 1 high from CFG-012, a new regression filed this pass), **ARCH A→A** (ARCH-011 now stale, 1 new low), **MAINT B→B** (1 new medium from MAINT-020 regression), **DOC A→B** (1 new medium from DOC-007 + DOC-004 moved to stale), **TEST B→B** (3 new stories filed: TEST-018 regression + TEST-019/-020), **TYPE A→A** (TYPE-011 new finding). **5 themes at B** (CORR, DOC, MAINT, PERF, TEST), **10 themes at A**, 0 at C, 0 at D. The 3 broken-YAML stories (OBS-007, SEC-011, OBS-009) are now repaired; OBS-007/OBS-009 marked verified (auth fix in `fa87bc6`); SEC-011 marked verified (dev-token validation in `9b4adb6`).

## Top concerns

High-severity open stories (focus areas for next tackling round):

1. **CORR-035** — Redis JobStore round-trip drops OutputFile.metadata (per-artifact contract from CORR-013 is broken at the persistence boundary) [high, S] — `correctness.md`
2. **CFG-012** — `WorkerCapabilities.max_input_tokens` is set by handlers but dropped at every wire boundary, so the orchestrator's plan-time check is silently bypassed [high, S] — `architecture.md`

M-effort open stories (still in the Round 2 design but not yet landed):

3. CORR-029 — `TranslateGemmaRunpodHandler._translate_batch` has no partial-success handling; mid-batch failure discards all completed work [medium, M] — `correctness.md` *(B15)*
4. EXC-001 — tenacity dependency is unused; `WorkerTimeoutError`/`PlanValidationError` are never raised [medium, M] — `code-quality.md` *(B23)*
5. MAINT-002 — `redis.py` hand-rolls JSON ser/deser for domain models that `cache.py` serializes via pydantic, duplicating and drifting [medium, M] — `code-quality.md` *(B19 — deferred)*
6. MAINT-011 — `create_worker_app` builds an `EdgeApp` only to copy its routes onto the outer app via path-string matching; the inner `EdgeApp` is dead code [medium, M] — `code-quality.md` *(B12)*
7. MAINT-015 — `inputs.py` is a near-verbatim copy of `artifacts.py` — same Protocol + three-variant shape duplicated 95% [medium, M] — `code-quality.md` *(B12)*
8. OBS-001 — Shutdown does not drain in-flight `_execute` tasks; cancelled jobs stay stuck at "running" [medium, M] — `operations.md` *(B10)*
9. REPRO-001 — `Redis.list_all()` returns non-deterministic order — step_handler worker selection is non-deterministic with Redis backend [medium, M] — `verification.md` *(B21)*
10. TEST-002 — `test_orchestrator_works_with_redis_backend` tests memory, not Redis — misleading name and no Redis coverage [medium, M] — `verification.md` *(B21)*
11. TEST-007 — `HealthMonitor._handle_failure` BOOTING→OFFLINE and OFFLINE→HEALTHY transitions are not covered [medium, M] — `verification.md` *(B10)*
12. TEST-014 — `workers/translategemma/tests/test_handler.py` does not cover the `model.generate` error path, partial-success, or `pad_token_id` init [medium, M] — `verification.md` *(B20)*
13. TEST-015 — `src/acheron/tls.py` (new top-level module, 114 lines) has no direct unit tests — only subprocess happy-path coverage [medium, M] — `verification.md` *(B20)*
14. TYPE-001 — `AcheronClient` returns `dict[str, Any]` consumed via magic-string keys; metadata contracts partially resolved [medium, M] — `code-quality.md` *(B17)*
15. TYPE-003 — `redis.py` accumulates 8 `# type: ignore[misc]` markers on `await self._redis.<method>()` calls [medium, M] — `code-quality.md` *(B17)*
16. DOC-007 — 24 source files have multi-line module docstrings that violate AGENTS.md's 1-line module-docstring rule [medium, M] — `surface.md`
17. CORR-012 — Health monitor trusts provider `BOOTING` status without bounding duration — step handler treats BOOTING as always-healthy [low, M] — `correctness.md` *(B10)*
18. CORR-032 — `TranslateGemmaRunpodHandler.handle` materializes the entire `chunks.json` in memory before validation [low, M] — `correctness.md` *(B15)*
19. CORR-033 — `TranslateGemmaRunpodHandler._translate_batch` mutates the shared processor's tokenizer in-place [low, M] — `correctness.md` *(B15)*
20. SEC-005 — Job submission/listing/capabilities routes require no authentication [low, M] — `operations.md` *(B24)*
21. TYPE-006 — `grpc.py` accumulates 5 `# type: ignore[...]` markers for the new proto `Artifact` oneof; needs a local `.pyi` stub [low, M] — `code-quality.md` *(B17)*
22. TYPE-007 — `RunPodForwarderHandler.__init__` calls `phantom_handler(settings)` under `# type: ignore[call-arg]`; needs a typed `RunPodHandlerProtocol` factory return [low, M] — `code-quality.md` *(B17)*
23. TYPE-008 — WorkerSDK has 14+ `Any`/`dict[str, Any]` annotations in 5 files [low, M] — `code-quality.md` *(B17 — superseded by TYPE-011)*
24. TYPE-010 — All three RunPod worker handlers type `self._model`/`self._processor` as `Any` with a stale-prone impl-phase comment — third instance of TYPE-009 [low, M] — `code-quality.md` *(B17)*
25. MAINT-020 — MAINT-009 fix reverted at 4 of 7 sites by 'fix: styling' commit; plus 1 new site introduced by EXC-004 fix [low, M] — `code-quality.md`

## Quick wins

S-effort open stories (low-risk, short fixes):

1. ARCH-011 — `worker_sdk/__init__.py` docstring falsely claims the module is GPU-SDK-free at import time [medium, S, stale] — `architecture.md` *(B12)*
2. ARCH-012 — `create_worker_app` cherry-picks routes from `EdgeApp.app.routes` via a hardcoded `inner_paths` set [medium, S] — `architecture.md` *(B12)*
3. ARCH-023 — Cross-module import of module-private `_ENV_ONLY_FIELDS` from `worker_sdk/settings.py` to `worker_sdk/config_loader.py` — same PLC2701 anti-pattern as the original ARCH-005 [low, S] — `architecture.md`
4. CORR-015 — `create_worker_app` cherry-picks routes from `EdgeApp` via hardcoded `inner_paths`; new routes silently dropped [medium, S] — `correctness.md` *(B12)*
5. DOC-003 — Configuration docs drift across README, `.env.example`, and an undocumented dashboard env var [medium, S] — `surface.md` *(B23)*
6. DOC-004 — README architecture tree, CI section, and Test paths omit the new `granite_speech` worker [medium, S, stale] — `surface.md`
7. DX-003 — `just install` does not install the new `workers/qwen3tts/` workspace member, breaking the documented fresh-clone setup [medium, S] — `surface.md` *(B23)*
8. DX-004 — `.envrc.example:5` uses `uv sync --all-extras` without `--all-packages`, so direnv-activated venvs also miss workspace members [medium, S] — `surface.md`
9. PERF-004 — `HealthMonitor._check_all` processes worker results sequentially with W Redis round-trips [medium, S] — `operations.md` *(B10)*
10. PERF-005 — Provider status checks in `_handle_failure` run sequentially and can starve the health interval [medium, S] — `operations.md` *(B10)*
11. PERF-007 — Per-call `httpx.AsyncClient` construction in health probes and pricing refresh (no connection reuse) [medium, S] — `operations.md` *(B10)*
12. PKG-002 — `pyproject.toml` dead `root_package` key + duplicate `soundfile` dev entry — drift artifacts from the workspace scaffold merge [low, S] — `surface.md` *(B23)*
13. PKG-003 — `Dockerfile:39` (certs-init stage) pins `cryptography~=49.0` while `pyproject.toml:168` pins `cryptography~=46.0` [low, S] — `surface.md` *(B23)*
14. PKG-004 — All three worker packages duplicate `pythonpath = ["../.."]` in `[tool.pytest.ini_options]`, masking the DX-003 workspace install gap [low, S] — `surface.md`
15. REPRO-003 — `tests/worker_sdk/conftest.py` `_no_sleep` fixture masks `asyncio.sleep` timing in retry/registration tests [low, S] — `verification.md` *(B21)*
16. SEC-019 — Edge `/execute` multipart branch returns 500 body with `error=str(exc)`, exposing raw exception detail (new instance of SEC-012) [low, S] — `operations.md` *(B24)*
17. DATA-007 — `_runpod_client` output.artifacts-not-list path and FileArtifact stream edge cases lack direct tests [low, S] — `verification.md`
18. TEST-005 — `_metadata_str` helper in `health.py` has no direct unit tests [low, S] — `verification.md` *(B20)*
19. TEST-006 — `HuggingFaceHealthProvider.check_status` has untested `str` and `else` branches [low, S] — `verification.md` *(B20)*
20. TEST-009 — `test_inputs.py` missing `Protocol` isinstance, `FileInput` missing-path, `StreamInput` empty, and `FileInput` empty-file edge cases [low, S] — `verification.md` *(B20)*
21. TEST-010 — `test_safe_chapter_id.py` missing unicode `chapter_id` coverage [low, S] — `verification.md` *(B20)*
22. TEST-011 — `test_cloud_audio.py` missing default-content_type and default-metadata branches in `make_runpod_handler` [low, S] — `verification.md` *(B20)*
23. TEST-018 — test_app.py still missing static-without-rate and registration_caps-passthrough tests (TEST-008 fix incomplete) [low, S] — `verification.md`
24. TEST-019 — TestFileArtifact class is undertested relative to TestBytesArtifact (1 test vs 4) [low, S] — `verification.md`
25. TEST-020 — test_pricing.py has no tests for `ZeroPrice.refresh()` and `StaticPrice.refresh()` (the no-op contract) [low, S] — `verification.md`
26. OBS-012 — Multipart parse-failure path in `_run_execute_multipart` returns 500 with no `logger.exception` — operator has no log evidence of parse failures [low, S] — `operations.md`
27. TYPE-011 — WorkerSDK `Any`/`dict[str, Any]` count is now 25 across 8 files — 2× larger than the 14+ figure in TYPE-008 [low, M] — `code-quality.md`

## Story counts

| Status | Count |
|---|---|
| open | 51 |
| in-progress | 0 |
| fixed | 52 |
| verified | 90 |
| stale | 3 |
| wontfix | 0 |
| broken-yaml | 0 |

Status deltas vs `a7aaf1e` (post-B11/B12/B19): no code change since a7aaf1e. This refresh pass: (1) resolved 81 `pending` SHA placeholders across all 6 bundle files by walking `git log --grep="(<ID>)"`, recovering the actual fix commits; (2) repaired 3 broken-YAML stories — OBS-007/OBS-009 marked verified (auth fix in `fa87bc6`); SEC-011 marked verified (dev-token validation in `9b4adb6`); (3) added 4 new stories for regressions found in Round 2 — CORR-034 (Python 2 except syntax re-introduced at 5 sites by `a7aaf1e`), MAINT-020 (same regression under MAINT lens), CFG-012 (CFG-011 fix broken at the wire boundary), TEST-018 (TEST-008 fix incomplete); (4) added 9 new findings — ARCH-023 (`_ENV_ONLY_FIELDS` cross-module import), CORR-035 (Redis metadata round-trip gap), TYPE-011 (25 `Any` annotations across worker_sdk, up from 14), TEST-019 (TestFileArtifact undertested), TEST-020 (no refresh() tests for ZeroPrice/StaticPrice), OBS-012 (multipart parse-failure log gap), DX-004 (`.envrc.example` missing `--all-packages`), PKG-004 (worker `pythonpath` hack), DOC-007 (24 multi-line module docstrings); (5) applied 14 staleness line-range updates; (6) marked 3 stories stale — ARCH-011 (docstring claim is now true), DOC-004 (README has been rewritten to include all 3 workers), PERF-008 (cited method removed but anti-pattern lives on at new location). The 3 previously broken-YAML stories (OBS-007, SEC-011, OBS-009) are now correctly verified with resolved `last_verified_at` and `fixed_in` SHAs.

## Changes since last review

This refresh is a no-code-change pass: the diff `a7aaf1e..77aadcd` is 1 commit, 1 file (`docs/code_review/summary.md`, the previous refresh's own commit). No code changed since the last review, so:
- 0 open-story regressions in the `verified`/`fixed`/`wontfix` immutable set — but 4 new regression stories filed as **new IDs** (CFG-012, CORR-034, MAINT-020, TEST-018) per the update-mode immutability rule, each linked to the original via `related: [<old-id>]`.
- 0 staleness flips caused by code change (the 3 new stale stories — ARCH-011, DOC-004, PERF-008 — are staleness-by-evolution: the cited code has been replaced or rewritten in ways that invalidate the original framing).
- 0 new findings of behaviour change. The 9 new findings are gaps that pre-existed but were uncovered by the audit pass: a fresh subagent sweep over each bundle's source + tests surfaced issues that the previous round-of-bundles work had not caught.

The next round of bundles (B25+ in the Round 2 design) should focus on: (1) CFG-012 + CORR-035 (the two new `high` open stories), (2) MAINT-020 + CORR-034 (the Python 2 except regression — small but cited twice to be sure the fix lands), (3) DOC-007 (24-file mechanical docstring trim), and (4) the TYPE cluster (TYPE-001, -003, -006, -007, -010, -011 — the worker_sdk `Any`/`# type: ignore` accumulation is the only remaining type-debt hotspot).

## Last orientation snapshot

**Repository**: acheron — audiobook processing pipeline (FastAPI orchestrator + gRPC/HTTP workers + Redis/memory stores). Greenfield (per AGENTS.md).

**Branch / HEAD**: `code-review-refresh` at `77aadcd` (off `master` at `a7aaf1e`; the only commit since is the prior summary refresh). 80 commits ahead of `c2a80fc` (the 8c post-review baseline); 62 commits ahead of `2b01434` (the B25 baseline).

**Top-level layout**: `src/acheron/core/` (domain models, errors, chunking, planner, interfaces), `src/acheron/shell/` (orchestrator, API, executors: streaming/async/sequential, stores: memory/redis, transports: http/grpc/local, cache, health, TLS, step_handler, local_handlers, capabilities, health_providers, config), `src/acheron/worker_sdk/` (base SDK for building workers — config_loader, _caps, _edge_http, _runpod_client, _server, registration, pricing, artifacts, cloud, handler, app, cli, settings, schemas, inputs), `src/acheron/tls.py` (top-level — TLS helpers shared by shell + worker_sdk + workers), `dashboard/` (separate package), `stubs/` (6 generic SDK-backed stubs + _sdk_base + nltk mock — `tts_volume_stub` deleted in B05), `workers/{qwen3tts,granite_speech,translategemma}/` (RunPod serverless workers, uv workspace members; the translategemma edge uses `workers/_shared_utils.py` after the B16 rename of `workers/_shared.py`), `workers/_shared_utils.py` + `workers/_shared/` (shared helpers — `safe_chapter_id`, `chunks.py` extracted in B14), `tests/` (mirrors src: tests/core, tests/shell, tests/worker_sdk, tests/integration, tests/scripts; plus stubs/tests/, workers/<pkg>/tests/).

**No hexagonal layers**: flat package structure. Interfaces (ABCs) in `core/interfaces.py`. No `ports.py` files. `HealthProvider` ABC now lives in `core/interfaces.py` (ARCH-009 verified in B12).

**Boundaries** (enforced by import-linter): `core` must NOT import `shell`; `worker_sdk` must NOT import `shell`; `workers` must NOT import `shell`. The `src/acheron/tls.py` is at the top level so both `shell` and `worker_sdk`/`workers` can consume it without violating the import-linter contract.

**Test landscape**: `tests/core/`, `tests/shell/{api,stores,transports}`, `tests/worker_sdk/` (20+ test files mirroring 14 source modules — new since the previous summary: `test_server.py` for B14's `run_worker_server`; `test_caps.py` for B18's collapsed helper), `tests/integration/`, `tests/scripts/`. New since the previous summary (9f9f3f5): +40 test cases across `test_app.py`, `test_edge_http.py`, `test_health.py`, `test_runpod_client.py`, `test_planner.py`, `test_server.py`, `test_schemas.py`, `test_caps.py`, `test_orchestrator.py`, `test_health_providers.py`, `test_step_handler.py`, `test_runpod_price.py`, `test_worker_store.py`, the 3 worker `test_handler.py` files, and `tests/integration/test_tls.py`.

**Tooling**: `just certs install lint-imports lint-strict proto test type-check type-check-pyright validate`. All deps `~=` pinned. uv workspace members: `workers/{qwen3tts, granite_speech, translategemma, _shared}`. PKG-003 (cryptography pin drift) remains: `Dockerfile:42-46` pins `cryptography~=49.0` while root `pyproject.toml:176` pins `cryptography~=46.0`; not fixed yet (B23).

**Key entry points**: `acheron.cli:main`, `acheron.worker_sdk.cli:main` (`acheron-worker-edge`), `acheron.shell.api.__main__`, `acheron.shell.api.app:create_app`. Worker runpod entrypoints: `workers/<pkg>/runpod_entrypoint:main`. Worker edge entrypoints: `worker_sdk/cli.py` (configurable via `WORKER_HOST` env var; expects `/app/<name>.worker.yaml`); both edge and runpod entrypoints now call `worker_sdk._server.run_worker_server` instead of inlining the uvicorn+TLS boilerplate (B14).
