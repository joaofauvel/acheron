---
branch: master
initial_review_commit: 23c29e1
last_updated_commit: 9f9f3f5
last_staleness_scan:
  commit: 9f9f3f5
  date: 2026-06-24
---

# Code Review Summary

## Per-theme grades

| Theme | Grade | Stories (open/in-progress/stale by severity) |
|---|---|---|
| ARCH | B | 0 critical, 0 high, 7 medium, 4 low |
| CORR | B | 0 critical, 0 high, 6 medium, 6 low |
| MAINT | B | 0 critical, 0 high, 6 medium, 8 low |
| PERF | B | 0 critical, 0 high, 4 medium, 1 low |
| TEST | B | 0 critical, 0 high, 6 medium, 8 low |
| DATA | A | 0 critical, 0 high, 2 medium, 1 low |
| DOC | A | 0 critical, 0 high, 2 medium, 1 low |
| DX | A | 0 critical, 0 high, 1 medium, 0 low |
| EXC | A | 0 critical, 0 high, 1 medium, 1 low |
| OBS | A | 0 critical, 0 high, 1 medium, 2 low |
| PKG | A | 0 critical, 0 high, 1 medium, 1 low |
| REPRO | A | 0 critical, 0 high, 1 medium, 1 low |
| SEC | A | 0 critical, 0 high, 2 medium, 7 low |
| TYPE | A | 0 critical, 0 high, 2 medium, 7 low |

Themes dropped from the rubric since the 8c baseline (all stories verified): **CFG (11 verified)**, **ML (0 verified)**, **MATH (0 verified)**.

Grade changes vs `c2a80fc` (the post-B03 summary baseline): **EXC C→A** (medium count 3→1, crosses under the 2-medium threshold for A); **OBS B→A** (medium count 3→1); **SEC B→A** (medium count 3→2, crosses back under the 2-medium threshold for A). All other themes unchanged. **No themes at C now** — the previous C-driver (SEC-008 critical) and the 8c-added 3-medium bursts in EXC and OBS are all resolved.

## Top concerns

1. CORR-018 — ASR multipart path materializes entire audio file in memory before streaming [medium, M] — `correctness.md`
2. CORR-019 — SDK edge `_parse_multipart_request` materializes entire body in memory [medium, M] — `correctness.md`
3. CORR-029 — `TranslateGemmaRunpodHandler._translate_batch` has no partial-success handling; mid-batch failure discards all completed work [medium, M] — `correctness.md`
4. ARCH-014 — `HttpWorker.execute()` branches on `WorkerType.ASR` to dispatch to a typed handler [medium, M] — `architecture.md`
5. ARCH-020 — `HttpWorker._execute_with_upstream_input` has a leaky triple-magic-string signature shared by three call sites [medium, M] — `architecture.md`
6. EXC-001 — tenacity dependency is unused; `WorkerTimeoutError`/`PlanValidationError` are never raised [medium, M] — `code-quality.md`
7. MAINT-002 — `redis.py` hand-rolls JSON ser/deser for domain models that `cache.py` serializes via pydantic, duplicating and drifting [medium, M] — `code-quality.md`
8. CORR-020 — `make_runpod_handler` silently coerces missing `data` field to empty bytes [medium, S] — `correctness.md`
9. ARCH-009 — `HealthProvider` ABC lives in `shell/health_providers.py` instead of `core/interfaces.py` [medium, S] — `architecture.md`
10. ARCH-011 — `worker_sdk/__init__.py` docstring falsely claims the module is GPU-SDK-compatible [medium, S] — `architecture.md`
11. ARCH-012 — `create_worker_app` cherry-picks routes from `EdgeApp.app.routes` via hardcoded `inner_paths` [medium, S] — `architecture.md`
12. ARCH-019 — `validate_chunking_fits_workers` is a post-step in `submit_job` that should fold into `compile_plan` [medium, S] — `architecture.md`
13. ARCH-021 — Identical uvicorn+TLS 7-line boilerplate duplicated across 4 entry points [medium, S] — `architecture.md`
14. CORR-009 — Step handler caches worker list and worker instances across steps [medium, S] — `correctness.md`
15. CORR-015 — `create_worker_app` cherry-picks routes from `EdgeApp` via hardcoded `inner_paths` [medium, S] — `correctness.md`
16. DATA-005 — `RedisWorkerStore._deserialize_worker` invalid status field has no corrupted-record path [medium, S] — `verification.md`
17. DATA-009 — `tests/core/test_planner.py:TestValidateChunkingFitsWorkers` has no boundary-condition test [medium, S] — `verification.md`
18. DOC-003 — Configuration docs drift across README, .env.example, and an undocumented env-var [medium, S] — `surface.md`
19. DOC-004 — README architecture tree, CI section, and Test paths omit the new `granite_speech` and `translategemma` workspaces [medium, S] — `surface.md`
20. DX-003 — `just install` does not install the new `workers/qwen3tts/` workspace members [medium, S] — `surface.md`

## Quick wins

1. CORR-020 — `make_runpod_handler` silently coerces missing `data` field to empty bytes [medium, S] — `correctness.md`
2. ARCH-009 — `HealthProvider` ABC lives in `shell/health_providers.py` [medium, S] — `architecture.md`
3. ARCH-011 — `worker_sdk/__init__.py` docstring falsely claims the module is GPU-SDK-compatible [medium, S] — `architecture.md`
4. ARCH-012 — `create_worker_app` cherry-picks routes from `EdgeApp.app.routes` [medium, S] — `architecture.md`
5. ARCH-015 — `step_cache` threaded through `default_worker_factory` even though only the HTTP branch consumes it [medium, S] — `architecture.md` *(resolved in B06; not on this list)*
6. ARCH-019 — `validate_chunking_fits_workers` is a post-step in `submit_job` that should fold into `compile_plan` [medium, S] — `architecture.md`
7. ARCH-021 — Identical uvicorn+TLS 7-line boilerplate duplicated across 4 entry points [medium, S] — `architecture.md`
8. CORR-009 — Step handler caches worker list and worker instances across steps [medium, S] — `correctness.md`
9. CORR-015 — `create_worker_app` cherry-picks routes from `EdgeApp` [medium, S] — `correctness.md`
10. DATA-005 — `RedisWorkerStore._deserialize_worker` invalid status field has no corrupted-record path [medium, S] — `verification.md`
11. DATA-009 — `tests/core/test_planner.py:TestValidateChunkingFitsWorkers` has no boundary-condition test [medium, S] — `verification.md`
12. DOC-003 — Configuration docs drift across README, .env.example, and an undocumented env-var [medium, S] — `surface.md`
13. DOC-004 — README architecture tree, CI section, and Test paths omit the new `granite_speech` and `translategemma` workspaces [medium, S] — `surface.md`
14. DX-003 — `just install` does not install the new `workers/qwen3tts/` workspace members [medium, S] — `surface.md`
15. MAINT-006 — Orchestrator.start() inlines 17-line registration-token block; logs the token in plaintext [medium, S] — `code-quality.md`
16. MAINT-007 — `RunPodHealthProvider` and `HuggingFaceHealthProvider` duplicate the HTTP fetch envelope [medium, S] — `code-quality.md`
17. MAINT-017 — chunks.json parsing duplicated byte-for-byte between qwen3tts and translategemma [medium, S] — `code-quality.md`
18. PERF-004 — HealthMonitor._check_all processes worker results sequentially with W Redis round-trips per tick [medium, S] — `operations.md`
19. PERF-005 — Provider status checks in `_handle_failure` run sequentially and can starve the health monitor [medium, S] — `operations.md`
20. PERF-006 — Edge `/execute` buffers entire multipart body in memory; O(n²) append for FileArtifact [medium, S] — `operations.md`
21. PERF-007 — Per-call `httpx.AsyncClient` construction in health probes and pricing refresh [medium, S] — `operations.md`
22. PKG-003 — `Dockerfile:39` (certs-init stage) pins `cryptography~=49.0` while `pyproject.toml:168` pins `cryptography~=46.0` [medium, S] — `surface.md`
23. SEC-014 — `worker.edge.yaml` default `orchestrator_url` is HTTP — registration token sent in cleartext [medium, S] — `operations.md`
24. SEC-016 — Granite-speech edge image default `orchestrator_url` is HTTP — registration token sent in cleartext [medium, S] — `operations.md`
25. TEST-016 — `workers/translategemma/tests/test_handler.py:235-241` class-level mutation anti-pattern [medium, S] — `verification.md`
26. TEST-017 — `tests/integration/test_tls.py` hardcodes 3 repo-relative paths via `Path(__file__).resolve().parents[2]` [medium, S] — `verification.md`

## Story counts

| Status | Count |
|---|---|
| open | 87 |
| in-progress | 0 |
| fixed | 3 |
| verified | 87 |
| stale | 3 |
| wontfix | 0 |
| broken-yaml | 3 |

Status deltas vs `c2a80fc` (post-B03): verified +18 (B04: 6 stories — CFG-003, -004, -005, -006, CORR-010, -011; B05: 4 stories — CFG-007, -008, -010, -011; B06: 2 stories — CFG-009, ARCH-015; B07: 6 stories — EXC-004, -005, OBS-005, -006, -008, SEC-013); stale +2 (ARCH-008, ARCH-013 — both became obsolete as a side-effect of CFG-004/CFG-006 fixes in B04); open -20. 3 stories have malformed YAML metadata (OBS-007, SEC-011, OBS-009 — status field renders as concatenated strings like `staleopen`); B24 of Round 2 will fix these as 1-line YAML updates. No previously `verified`/`fixed`/`wontfix` stories regressed. Note: the **CFG theme is now fully verified** (11 stories) and has been dropped from the per-theme rubric.

## Changes since last review

The diff `c2a80fc..9f9f3f5` (12 commits, 53 files, +1304/-1044) covers Round 2 bundles B04–B07. The substantive code changes are concentrated in:

- `src/acheron/shell/config.py` (B04: `UnsetEnvVarError` + `_resolve_env_var` for `${VAR}` expansion that raises on unset; `OrchestratorSettings.open_registration` field; expanded `_ENV_VAR_PATTERN` to POSIX charset with `${VAR:-default}` escape-hatch; `_EnvAliasSettingsSource` maps `ACHERON_OPEN_REGISTRATION=1` to `orchestrator.open_registration=True`)
- `src/acheron/shell/orchestrator.py` (B04: drop in-place `data_dir` mutation; `Orchestrator.__init__` builds fresh `Settings` via `model_copy(update=...)`; B06: drop `step_cache` arg from `create_step_handler` call)
- `src/acheron/shell/api/app.py` (B04: drop in-place `data_dir` mutation in `create_app`; build fresh `Settings` via `model_copy`)
- `src/acheron/shell/api/deps.py` (B04: drop `os.environ.get('ACHERON_OPEN_REGISTRATION')`; read `orch.settings.orchestrator.open_registration` instead)
- `src/acheron/shell/transports/{http,grpc}.py` (B04: drop `os.environ.get('ACHERON_DATA_DIR')` fallback; require `data_dir` as a kwarg)
- `src/acheron/shell/step_handler.py` (B04: add `data_dir` kwarg to `default_worker_factory`/`create_step_handler`; B06: drop `step_cache` kwarg; `HttpWorker` constructs its own default `StepCache` from `data_dir`)
- `src/acheron/worker_sdk/_runpod_client.py` (B04: `base_url` param replaces `os.environ.get('ACHERON_WORKER__RUNPOD_BASE_URL')`; B07: `try/except` + `logger.exception` for both `_open_endpoint` and `endpoint.run` paths)
- `src/acheron/worker_sdk/{app,cli}.py` (B04: `settings.worker_host`, `settings.log_level` replace direct env reads; B07: narrow `BaseException` catch in lifespan to `(httpx.HTTPError, OSError, KeyError, ValueError, TypeError)`; add `logger.exception` with class name)
- `src/acheron/worker_sdk/_edge_http.py` (B07: narrow `BaseException` catch in `_dispatch` to `Exception`; add `logger.exception` with handler name; KeyboardInterrupt/SystemExit/CancelledError now propagate)
- `src/acheron/worker_sdk/pricing.py` (B07: `try/except` + `logger.exception` for `RunPodPrice._refresh_rate`; **SEC-013: move `api_key` from URL `params` to `Authorization: Bearer` header**)
- `src/acheron/worker_sdk/settings.py` (B04: add `worker_host` (with `AliasChoices('worker_host', 'WORKER_HOST')`), `log_level`, `runpod_base_url` fields; B05: drop `output_mode` + `output_volume_dir` fields; drop `_validate_composite` validator; add `max_input_tokens: int | None = None`)
- `src/acheron/core/planner.py` (B06: drop `chars_per_token: int = 1` default from `validate_chunking_fits_workers` signature; now required)
- `src/acheron/shell/health_providers.py` (B07: add `logger.warning` before falling back in both `RunPodHealthProvider` and `HuggingFaceHealthProvider` catch blocks; logs `provider_name`, `endpoint_id`, `exc_class`, and the exception message)
- 3 worker handlers in `workers/{qwen3tts,granite_speech,translategemma}/handler.py` (B05: wire `self._settings.model_id` in qwen3tts + granite_speech; wire `self._settings.max_input_tokens` in qwen3tts + translategemma; remove `_MODEL_ID`/`_MAX_INPUT_TOKENS` constants)
- 6 worker YAMLs + 4 stub YAMLs (B05: drop `output_mode` + `output_volume_dir`; `tts_volume_stub` package deleted; `docker-compose.yml` cleaned)

New test files / test methods: `tests/shell/test_config.py` (+9 new tests for B04 env-var expansion), `tests/shell/test_orchestrator.py` (+2 mutation-isolation tests), `tests/worker_sdk/test_settings.py` (+7 new tests for B05 field removals + `max_input_tokens`), `tests/worker_sdk/test_app.py` (+2 lifespan tests, +2 endpoint-url tests, +1 step_cache fallback test, +1 short-circuit test), `tests/worker_sdk/test_runpod_client.py` (+2 exception-logging tests), `tests/worker_sdk/test_runpod_price.py` (+1 exception-logging test, +1 SEC-013 header test), `tests/worker_sdk/test_edge_http.py` (+1 KeyboardInterrupt-propagation test, +1 handler-error log test), `tests/shell/test_health_providers.py` (+2 warning-log tests), `tests/core/test_planner.py` (+1 required-arg test), `tests/shell/test_step_handler.py` (+2 default-factory tests for the dropped `step_cache` kwarg), `workers/<pkg>/tests/test_capabilities.py` (+7 new tests for B05 field wiring).

Bookkeeping: 6 stories moved to `verified` in B04; 4 in B05; 2 in B06; 6 in B07. 2 stories marked `stale` in B04 (ARCH-008, ARCH-013). **~80 other open stories** had `last_verified_at.commit` and `lines:` updated to post-fix locations across the touched files. **Note:** MAINT-009 had 3 of its 7 `except A, B:` sites incidentally fixed in B07 (the two `health_providers.py` providers + `pricing.py`); the 4 remaining sites in `http.py`, `local_handlers.py`, `streaming.py`, `cache.py` are still open and tracked in B19.

Dominant themes (this delta):

- **CFG (B → removed from rubric)** — All 11 CFG stories verified across B04 (CFG-003, -004, -005, -006, CORR-010, -011), B05 (CFG-007, -008, -010, -011), and B06 (CFG-009). CFG was the most settings-sprawled theme and is now fully resolved.
- **EXC (B → A)** — 2 of 3 EXC stories verified in B07 (EXC-004, EXC-005). EXC-001 (tenacity dependency unused) remains open; the 1-medium A-grade threshold is met.
- **OBS (B → A)** — 3 of 4 OBS stories verified in B07 (OBS-005, -006, -008). 1 medium remains (OBS-001 — health-probe silence on retryable failures).
- **SEC (B → A)** — 1 of 10 SEC stories verified in B07 (SEC-013 — RunPod API key to Authorization header). The 2-medium A-grade threshold is met.
- **ARCH (B)** — 1 story verified in B06 (ARCH-015 — `step_cache` no longer threaded through factory). 2 stories marked stale as side-effect of CFG-004/CFG-006 (ARCH-008, ARCH-013).
- **MAINT (B)** — No stories directly verified; 1 fixed in Round 1 (MAINT-016); 1 indirectly addressed (MAINT-007 envelope duplication partially de-duped by shared `logger.warning` call in B07).
- **CORR / DATA / DOC / PERF / TEST / TYPE / REPRO / PKG / DX** — Unchanged themes (no stories verified in this delta, but line ranges refreshed to post-fix locations).

10 themes at A (ARCH→B now; CORR, MAINT, PERF, TEST still at B), 5 themes at B (ARCH, CORR, MAINT, PERF, TEST), 0 themes at C. The 3 broken-YAML stories (OBS-007, SEC-011, OBS-009) are still scheduled for B24 of Round 2.

## Last orientation snapshot

**Repository**: acheron — audiobook processing pipeline (FastAPI orchestrator + gRPC/HTTP workers + Redis/memory stores). Greenfield (per AGENTS.md).

**Branch / HEAD**: `master` at `9f9f3f5` (FF-merged from `fix/code-review-tackle-2` after B07). Pushed to `origin` (codeberg + github). 18 commits ahead of `c2a80fc` (the 8c post-review baseline).

**Top-level layout**: `src/acheron/core/` (domain models, errors, chunking, planner, interfaces), `src/acheron/shell/` (orchestrator, API, executors: streaming/async/sequential, stores: memory/redis, transports: http/grpc/local, cache, health, TLS, step_handler, local_handlers, capabilities, health_providers, config), `src/acheron/worker_sdk/` (base SDK for building workers — config_loader, _edge_http, _runpod_client, registration, pricing, artifacts, cloud, handler, app, cli, settings, schemas, inputs), `src/acheron/tls.py` (top-level — TLS helpers shared by shell + worker_sdk + workers), `dashboard/` (separate package), `stubs/` (6 generic SDK-backed stubs + _sdk_base + nltk mock — `tts_volume_stub` deleted in B05), `workers/{qwen3tts,granite_speech,translategemma}/` (RunPod serverless workers, uv workspace members), `workers/_shared.py` + `workers/_shared/` (shared helpers — `safe_chapter_id`), `tests/` (mirrors src: tests/core, tests/shell, tests/worker_sdk, tests/integration, tests/scripts; plus stubs/tests/, workers/<pkg>/tests/).

**No hexagonal layers**: flat package structure. Interfaces (ABCs) in `core/interfaces.py`. No `ports.py` files. `HealthProvider` ABC still in `shell/health_providers.py` (ARCH-009 open).

**Boundaries** (enforced by import-linter): `core` must NOT import `shell`; `worker_sdk` must NOT import `shell`; `workers` must NOT import `shell`. The `src/acheron/tls.py` is at the top level so both `shell` and `worker_sdk`/`workers` can consume it without violating the import-linter contract.

**Test landscape**: `tests/core/`, `tests/shell/{api,stores,transports}`, `tests/worker_sdk/` (20+ test files mirroring 14 source modules), `tests/integration/`, `tests/scripts/`. New since the previous summary (c2a80fc): +30 test cases across `test_config.py`, `test_orchestrator.py`, `test_settings.py`, `test_app.py`, `test_runpod_client.py`, `test_runpod_price.py`, `test_edge_http.py`, `test_health_providers.py`, `test_planner.py`, `test_step_handler.py`, and the 3 worker `test_capabilities.py` files.

**Tooling**: `just certs install lint-imports lint-strict proto test type-check type-check-pyright validate`. All deps `~=` pinned. uv workspace members: `workers/{qwen3tts, granite_speech, translategemma, _shared}`. PKG-003 (cryptography pin drift) remains: `Dockerfile:39` pins `cryptography~=49.0` while root `pyproject.toml:168` pins `cryptography~=46.0`; not fixed yet.

**Key entry points**: `acheron.cli:main`, `acheron.worker_sdk.cli:main` (`acheron-worker-edge`), `acheron.shell.api.__main__`, `acheron.shell.api.app:create_app`. Worker runpod entrypoints: `workers/<pkg>/runpod_entrypoint:main`. Worker edge entrypoints: `worker_sdk/cli.py` (configurable via `WORKER_HOST` env var; expects `/app/<name>.worker.yaml`).

**Changes since last review** (delta brief): the diff `c2a80fc..9f9f3f5` is 12 commits, 53 files, +1304/-1044. B04: `ACHERON_OPEN_REGISTRATION` now flows through Settings; in-place `data_dir` mutations replaced with `model_copy`; 5 direct env-var reads in transports/worker_sdk replaced with explicit settings; `${VAR}` expansion now raises `UnsetEnvVarError` on unset (preserves `${VAR:-default}` escape-hatch); accepts lowercase env var names per POSIX. B05: `model_id` wired in qwen3tts + granite_speech handlers; `output_mode` + `output_volume_dir` dropped from `WorkerSettings` and all YAMLs; `tts_volume_stub` deleted; `max_input_tokens` now sourced from `WorkerSettings`. B06: `chars_per_token` function-level default dropped (now required); `step_cache` no longer threaded through `default_worker_factory`/`create_step_handler` (HttpWorker constructs its own). B07: `BaseException` catches narrowed to typed errors in lifespan + `_dispatch`; health-provider + RunPod client/pricing failures now log with context (provider_name, endpoint_id, exc_class); **RunPod API key moved from URL query param to `Authorization: Bearer` header**. 18 stories verified total this delta, 2 stale (ARCH-008, ARCH-013), 0 regressions.

**Hand off to `code-review-tackle` for remaining open stories.** B08-B25 of Round 2 remain on the worktree branch `fix/code-review-tackle-2`. All high+critical stories are now resolved; the most impactful remaining items to tackle first (medium-severity, mostly S-effort):
1. CORR-018 (medium, M) — stream the ASR multipart body instead of materializing
2. CORR-019 (medium, M) — stream the SDK edge multipart body instead of materializing
3. CORR-029 (medium, M) — add partial-success handling to `_translate_batch`
4. ARCH-014 (medium, M) — replace `WorkerType.ASR` branch with match-based dispatch
5. ARCH-020 (medium, M) — replace leaky triple-magic-string signature with `StepDispatch` table
6. EXC-001 (medium, M) — wire tenacity retries OR remove the dead dependency and unused exceptions
7. MAINT-002 (medium, M) — replace hand-rolled redis.py JSON ser/deser with pydantic serializer
8. SEC-005 (medium, S) — make worker registration fail-closed when `open_registration` is False
9. SEC-006 (medium, S) — make `/execute` bearer check unconditional
10. B24 will fix the 3 broken-YAML stories (OBS-007, SEC-011, OBS-009) as 1-line YAML updates.
