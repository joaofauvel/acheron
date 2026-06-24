---
branch: chore/code-review-update
initial_review_commit: 23c29e1
last_updated_commit: eb6849c85d83f2277eb450f18a11e63cae2defd1
last_staleness_scan:
  commit: eb6849c85d83f2277eb450f18a11e63cae2defd1
  date: 2026-06-24
---

# Code Review Summary

## Per-theme grades

| Theme | Grade | Stories (open/in-progress/stale) |
|---|---|---|
| CORR | C | 0 critical, 1 high, 11 medium, 13 low |
| ML | A | 0 critical, 0 high, 0 medium, 0 low |
| MATH | A | 0 critical, 0 high, 0 medium, 0 low |
| ARCH | B | 0 critical, 2 high, 8 medium, 5 low |
| CFG | B | 0 critical, 0 high, 8 medium, 1 low |
| MAINT | B | 0 critical, 0 high, 7 medium, 8 low |
| EXC | B | 0 critical, 0 high, 3 medium, 1 low |
| TYPE | A | 0 critical, 0 high, 2 medium, 7 low |
| TEST | B | 0 critical, 0 high, 6 medium, 8 low |
| REPRO | A | 0 critical, 0 high, 1 medium, 1 low |
| DATA | B | 0 critical, 0 high, 4 medium, 1 low |
| PERF | B | 0 critical, 0 high, 4 medium, 1 low |
| OBS | B | 0 critical, 0 high, 6 medium, 3 low |
| SEC | C | 1 critical, 6 high, 4 medium, 8 low |
| DX | A | 0 critical, 0 high, 1 medium, 0 low |
| PKG | A | 0 critical, 0 high, 1 medium, 1 low |
| DOC | B | 0 critical, 0 high, 3 medium, 1 low |

Grade changes vs `e544584`: CORR B→C (medium count 7→11, crosses 9-15 medium threshold), TEST A→B (medium count 2→6, crosses ≤2 medium threshold), DOC A→B (medium count 2→3, crosses ≤2 medium threshold). All other themes unchanged.

## Top concerns

1. SEC-008 — Auto-generated registration token is logged in plaintext at startup [critical] — `operations.md`
2. SEC-022 — `translategemma-edge` compose service hardcodes `${ACHERON_REGISTRATION_TOKEN:-dev-registration-token}` fallback (new instance of SEC-011) [high] — `operations.md`
3. SEC-023 — Translategemma edge `phantom_handler` import path requires `workers/translategemma/handler.py` on PYTHONPATH, but `Dockerfile.edge` does not copy it — edge service is broken by design [high] — `operations.md`
4. CORR-014 — `RunPodClient.run` silently treats a FAILED RunPod job as a successful empty result [high] — `correctness.md`
5. SEC-007 — Host Path Traversal & Arbitrary Local File Read in ExtractionHandler [high] — `operations.md`
6. SEC-009 — Registration token file created with process umask (potentially world-readable) [high] — `operations.md`
7. SEC-011 — `ACHERON_REGISTRATION_TOKEN` defaults to publicly-known `dev-registration-token` in compose and `.env.example` [high] — `operations.md`
8. ARCH-017 — `shell/tls.py` is a 24-line back-compat shim re-exporting `acheron.tls` — direct AGENTS.md greenfield violation [high] — `architecture.md`
9. ARCH-018 — `ChunkingTooLongForWorkerError` is a subclass of `InvalidLanguagePathError` for back-compat reasons that don't exist — codifies a documentation-via-runtime-error contract [high] — `architecture.md`
10. SEC-018 — `granite-speech-edge` compose service hardcodes `:-dev-registration-token` fallback (new instance of SEC-011) [high] — `operations.md`

## Quick wins

1. SEC-008 — Auto-generated registration token is logged in plaintext at startup [critical, S effort] — `operations.md`
2. CORR-014 — `RunPodClient.run` silently treats a FAILED RunPod job as a successful empty result [high, S effort] — `correctness.md`
3. SEC-009 — Registration token file created with process umask (potentially world-readable) [high, S effort] — `operations.md`
4. SEC-011 — `ACHERON_REGISTRATION_TOKEN` defaults to publicly-known `dev-registration-token` in compose and `.env.example` [high, S effort] — `operations.md`
5. SEC-022 — `translategemma-edge` compose service hardcodes `${ACHERON_REGISTRATION_TOKEN:-dev-registration-token}` fallback (new instance of SEC-011) [high, S effort] — `operations.md`
6. SEC-018 — `granite-speech-edge` compose service hardcodes `:-dev-registration-token` fallback (new instance of SEC-011) [high, S effort] — `operations.md`
7. SEC-023 — Translategemma edge `phantom_handler` import path requires `workers/translategemma/handler.py` on PYTHONPATH, but `Dockerfile.edge` does not copy it — edge service is broken by design [high, S effort] — `operations.md`
8. ARCH-017 — `shell/tls.py` is a 24-line back-compat shim re-exporting `acheron.tls` — direct AGENTS.md greenfield violation [high, S effort] — `architecture.md`
9. ARCH-018 — `ChunkingTooLongForWorkerError` is a subclass of `InvalidLanguagePathError` for back-compat reasons that don't exist — codifies a documentation-via-runtime-error contract [high, S effort] — `architecture.md`
10. CORR-031 — `HttpWorker.health` uses deprecated Python 2 `except E1, E2:` syntax [low, S effort] — `correctness.md`
11. CORR-026 — `chars_per_token=4` default under-estimates CJK tokens; docstring claim is inverted [medium, S effort] — `correctness.md`
12. CORR-027 — `_execute_with_upstream_input` only POSTs the first matching file; multi-file upstream outputs are silently truncated [medium, S effort] — `correctness.md`
13. CORR-028 — `_parse_multipart` boundary extraction raises IndexError on response missing `boundary=` [medium, S effort] — `correctness.md`
14. CORR-030 — `_parse_multipart` takes the first `application/json` part as metrics; a sidecar JSON part would be silently overwritten [low, S effort] — `correctness.md`
15. DOC-005 — `shell/tls.py` shim docstring violates greenfield rule; references past move and old import path [medium, S effort] — `surface.md`
16. ARCH-019 — `validate_chunking_fits_workers` is a post-step in `submit_job` that should be folded into `compile_plan` [medium, S effort] — `architecture.md`
17. ARCH-022 — `HttpWorker._post_multipart` is a near-byte-duplicate of `HttpWorker._request` — should be a one-liner wrapper [low, S effort] — `architecture.md`
18. CFG-009 — `Settings.chars_per_token` is a top-level knob consumed by exactly one function and duplicated in two defaults [medium, S effort] — `architecture.md`
19. CFG-010 — `WorkerSettings.model_id` is now consumed only by `translategemma` — qwen3tts and granite_speech still hard-code the value, widening the CFG-007/008 silence from 4 YAMLs to 6 [medium, S effort] — `architecture.md`
20. CFG-011 — `WorkerCapabilities.max_input_tokens` is published in capabilities() by 2 workers but only consumed in 1 place (the planner) — value is hard-coded in handlers, not configurable via WorkerSettings [low, S effort] — `architecture.md`
21. MAINT-016 — `ChunkingTooLongForWorkerError` subclasses `InvalidLanguagePathError` — inheritance used as a type-tag dispatch mechanism [medium, S effort] — `code-quality.md`
22. MAINT-017 — chunks.json parsing duplicated byte-for-byte between qwen3tts and translategemma handlers — third instance of the wire-shape drift pattern [medium, S effort] — `code-quality.md`
23. MAINT-018 — Per-chunk field validation duplicated between translategemma (`_normalize_chunk`) and qwen3tts (`_chunk_text` / `_chunk_chapter_id`); shared `Chunk` dataclass would unify them [low, S effort] — `code-quality.md`
24. MAINT-019 — `TranslateGemmaRunpodHandler.handle` is 54 lines (over 50) and bundles 3 distinct concerns: validation, parsing, inference + artifact building [low, S effort] — `code-quality.md`
25. TYPE-010 — All three RunPod worker handlers type self._model/self._processor as `Any` with a stale-prone impl-phase comment — third instance of TYPE-009 [low, M effort] — `code-quality.md`
26. SEC-020 — Translategemma `Dockerfile.runpod` runs as root — no USER directive (new instance of SEC-015/SEC-017) [low, S effort] — `operations.md`
27. SEC-021 — Translategemma `worker.edge.yaml` default `orchestrator_url` is HTTP — registration token sent in cleartext (new instance of SEC-014/SEC-016) [medium, S effort] — `operations.md`
28. OBS-010 — `translategemma-edge` service exposes `/execute` on host port 8009 — unauthenticated (new instance of OBS-007/OBS-009) [medium, S effort] — `operations.md`
29. OBS-011 — `validate_chunking_fits_workers` runs in `submit_job` with no log on success or failure — operator cannot confirm the plan-time input-budget check ran [low, S effort] — `operations.md`
30. TEST-014 — `workers/translategemma/tests/test_handler.py` does not cover the model.generate error path, partial-success, or pad_token_id init [medium, M effort] — `verification.md`
31. TEST-015 — `src/acheron/tls.py` (new top-level module, 114 lines) has no direct unit tests — only subprocess happy-path coverage [medium, M effort] — `verification.md`
32. TEST-016 — `workers/translategemma/tests/test_handler.py:235-241` class-level mutation anti-pattern — second instance of open TEST-012 [medium, S effort] — `verification.md`
33. TEST-017 — `tests/integration/test_tls.py` hardcodes 3 repo-relative paths via `Path(__file__).resolve().parents[2]` — new brittleness introduced in this delta [medium, S effort] — `verification.md`
34. DATA-009 — `tests/core/test_planner.py:TestValidateChunkingFitsWorkers` has no boundary-condition test (==, one-over, max_input_tokens=0, empty caps) [medium, S effort] — `verification.md`
35. DOC-006 — `submit_job` and `validate_chunking_fits_workers` have incomplete Google-style `Raises:` sections after the 8c plan-time check [low, S effort] — `surface.md`
36. CORR-029 — `TranslateGemmaRunpodHandler._translate_batch` has no partial-success handling; mid-batch failure discards all completed work [medium, M effort] — `correctness.md`
37. CORR-032 — `TranslateGemmaRunpodHandler.handle` materializes the entire chunks.json in memory before validation [low, M effort] — `correctness.md`
38. CORR-033 — `TranslateGemmaRunpodHandler._translate_batch` mutates the shared processor's tokenizer in-place [low, M effort] — `correctness.md`
39. ARCH-020 — `HttpWorker._execute_with_upstream_input` has a leaky triple-magic-string signature shared by three call sites [medium, M effort] — `architecture.md`
40. ARCH-021 — Identical uvicorn+TLS 7-line boilerplate duplicated across 4 entry points after the worker-side TLS rollout [medium, S effort] — `architecture.md`
41. PERF-007 — Per-call `httpx.AsyncClient` construction in health probes and pricing refresh (no connection reuse) [medium, S effort] — `operations.md`
42. PERF-008 — `HttpWorker._post_multipart` constructs a new `httpx.AsyncClient` per call (new instance of PERF-007) [low, S effort] — `operations.md`

## Story counts

| Status | Count |
|---|---|
| open | 138 |
| in-progress | 0 |
| fixed | 2 |
| verified | 43 |
| stale | 0 |
| wontfix | 0 |

Status deltas vs `e544584`: fixed +1 (MAINT-010 transitioned to `fixed` in `eb6849c` — duplicate docstring removed); open +30 (8 CORR + 6 ARCH + 3 CFG + 4 MAINT + 1 TYPE + 4 TEST + 1 DATA + 4 SEC + 2 OBS + 2 DOC); verified unchanged. The 30 new findings surface 8c-specific issues, plus the 7 pre-existing findings re-resolved (CORR-013, 018, MAINT-005, 009, 011, DATA-006, 008, PERF-007, OBS-001, 007, 009, SEC-006, 011, 018, plus several line-shift-only re-resolutions).

## Changes since last review

The diff `e544584..eb6849c` (31 commits, 51 files, 7757 insertions / 84 deletions) covers Layer 8c: the new `translategemma` RunPod serverless translation worker (handler 315 lines, runpod_entrypoint 35 lines, Dockerfile.runpod 59 lines, pyproject, README 97 lines, worker.yaml + worker.edge.yaml, 3 test files totaling 478 lines), the plan-time `validate_chunking_fits_workers` check (new `ChunkingTooLongForWorkerError` in `core/errors.py:16-22`, `Settings.chars_per_token` config knob, `WorkerCapabilities.max_input_tokens` field, the `submit_job` call in `orchestrator.py:244-249`), the new top-level `src/acheron/tls.py` (114 lines) extracted from `shell/tls.py` (now a 24-line back-compat shim — see ARCH-017), the worker-side TLS rollout (`worker_sdk/cli.py:10` import, `stubs/tts_grpc_stub/main.py:21-28` and `stubs/tts_local_stub/main.py:21-28` updated to pass uvicorn SSL kwargs), the `HttpWorker.execute()` match-based dispatch with the shared `_execute_with_upstream_input` helper and new `_post_multipart` method, qwen3tts reading chunks from `Input` and publishing `max_input_tokens=2048`, the `translategemma-edge` compose service under the `runpod-translation` profile (port 8009:8001, see SEC-022 + OBS-010), the new `build-translategemma` GHCR job, and the worker-side TLS integration tests now unblocked in `tests/integration/test_tls.py`.

36 new findings surfaced: 8 CORR, 6 ARCH, 3 CFG, 4 MAINT, 1 TYPE, 4 TEST, 1 DATA, 4 SEC, 2 OBS, 2 DOC. The dominant pattern across 8c is "third instance" — the new translategemma worker widens the SEC-011 dev-token fallback, the SEC-014/16 HTTP default, the SEC-015/17 root-user Dockerfile, the OBS-007/09 unauthenticated /execute host port, the PERF-007/08 per-call AsyncClient, and the SEC-012 raw `str(exc)` in 500 body. The new `ChunkingTooLongForWorkerError` subclass relationship (ARCH-018 / MAINT-016) and the `shell/tls.py` back-compat shim (ARCH-017 / DOC-005) are the two new greenfield-rule violations that the 8c layer introduced; both are high-severity and should be fixed before the next layer widens the pattern further. The Dockerfile.edge omission (SEC-023) is the most operationally significant new finding: the new `translategemma-edge` compose service is broken by design because the Dockerfile.edge delta was omitted. The MAINT-017/018 (chunks.json parsing + per-chunk validation duplicated) and TYPE-010 (3rd `Any`-typed self._model) findings are consolidation pressure that should be addressed via a `parse_chunks_json(input)` helper and a shared `_ModelProto`/`_ProcessorProto` Protocol before a 4th worker package copies the same shape. No previously verified/fixed/wontfix stories regressed. 7 line-shift re-resolutions (CORR-013, 018; MAINT-005, 009, 011; DATA-006, 008) and 1 line re-resolution (ARCH-014 was updated to the new match-based dispatch + the new helper). 1 transition to `fixed` (MAINT-010).

Dominant themes:

- **CORR (B → C, 1 high / 11 medium / 13 low)** — CORR-014 (high) remains the most significant existing finding: `RunPodClient.run` never inspects `output.status`, so a FAILED RunPod job propagates as a successful empty `/execute` response. The 8c translategemma work widens the finding to a 3rd worker (the worker package is now load-bearing for the same RunPod forwarder). Layer 8c adds 4 new medium CORR findings: CORR-026 (chars_per_token=4 default under-estimates CJK tokens — the docstring's "conservative" claim is inverted; for a 4000-char CJK chunk the check passes but the worker receives 4000 tokens and may OOM), CORR-027 (`_execute_with_upstream_input` only POSTs the first matching file; multi-file upstream outputs are silently truncated), CORR-028 (`_parse_multipart` `boundary=` extraction raises `IndexError` on a Content-Type missing the parameter; the orchestrator should mirror the edge's defensive `WorkerError` raise), CORR-029 (`_translate_batch` has no partial-success handling; a mid-batch OOM discards all previously translated batches). The Python 2 `except A, B:` syntax in `HttpWorker.health:239` (CORR-031) parses correctly today but is deprecated style and asymmetric with the two other except blocks in the same file. The chunks.json full-materialization in `TranslateGemmaRunpodHandler.handle:187` (CORR-032) is the 3rd instance of the request-side memory-cliff pattern (after orchestrator CORR-018 and SDK CORR-019); the RunPod forwarder always wraps in `BytesInput`, so a future multi-MB chapter would force a rewrite. CORR-033 finds a latent tokenizer state mutation in `_translate_batch` (lines 267-269) that is benign for the single-handler RunPod case but would not survive a model hot-reload or a multi-worker orchestrator.

- **ARCH (B, 2 high / 8 medium / 5 low)** — Two new high-severity findings: ARCH-017 (`shell/tls.py` is a 24-line back-compat shim re-exporting `acheron.tls` — direct AGENTS.md greenfield-rule violation; 7 import sites still use the old path, the shim should be deleted and the callers migrated), ARCH-018 (`ChunkingTooLongForWorkerError` is a subclass of `InvalidLanguagePathError` for back-compat reasons that don't exist; `git grep 'except InvalidLanguagePathError' src/ tests/ dashboard/` returns zero consumers — the subclass relationship is exactly the "documentation-via-runtime-error contract" pattern AGENTS.md calls out). Three new mediums: ARCH-019 (`validate_chunking_fits_workers` is a post-step in `submit_job` that should fold into `compile_plan` — first time the shell layer passes orchestrator-side settings into a core validator; the seam grows with each new plan-time check), ARCH-020 (`_execute_with_upstream_input` has a leaky triple-magic-string signature shared by 3 call sites — `upstream_step`/`content_type_predicate`/`form_field` must stay in lockstep at every call), ARCH-021 (uvicorn+TLS 7-line boilerplate duplicated across 4 entry points after the worker-side TLS rollout). The `validate_chunking_fits_workers` + `_execute_with_upstream_input` pair makes the Layer 8c the moment to enforce the "use typing in your favor" rule. The 8c `compile_plan` post-step (ARCH-019) widens the existing boundary maintenance burden. The translategemma workspace member is clean (hatchling member, follows qwen3tts/granite_speech pattern, reuses `workers._shared.safe_chapter_id`); the only divergence is the hard-coded `_MAX_INPUT_TOKENS = 2048` instead of `WorkerSettings.max_input_tokens` (see CFG-011).

- **SEC (C, 1 critical / 6 high / 4 medium / 8 low)** — grade stays C (still driven by SEC-008 critical, the auto-generated token logged in plaintext at startup). The 8c work added 4 new SEC findings, all third-instances: SEC-020 (translategemma Dockerfile.runpod no USER directive — same as SEC-015/17), SEC-021 (translategemma worker.edge.yaml HTTP default — same as SEC-014/16), SEC-022 (translategemma-edge compose `${ACHERON_REGISTRATION_TOKEN:-dev-registration-token}` fallback — same as SEC-011/18 — 4th compose service now defaults to the same publicly-known token), SEC-023 (translategemma edge `phantom_handler` import path requires `workers/translategemma/handler.py` on PYTHONPATH, but `Dockerfile.edge:26-37` only copies qwen3tts/granite_speech — the new compose service is broken by design). SEC-023 is the most operationally significant new finding: a deployer following the new `workers/translategemma/README.md` will get a broken service, attempt to debug, and may weaken security to make it work. The dominant new risk is the dev-default bypass broadening from 3 compose services to 4 (SEC-022).

- **OBS (B, 6 medium / 3 low)** — OBS-010 (medium) is the most operationally significant new finding: the new `translategemma-edge` compose service maps `8009:8001` on the host network, exposing the unauthenticated POST `/execute` endpoint that forwards to a RunPod serverless endpoint that bills the operator. Same pattern as OBS-007 (qwen3tts-edge on 8004) and OBS-009 (granite-speech-edge on 8008). The host-port anti-pattern is now 3 services. OBS-011 (low) finds the new `validate_chunking_fits_workers` call silent on both success and failure paths — operators cannot confirm the plan-time input-budget check ran from the orchestrator logs alone.

- **MAINT (B, 7 medium / 8 low)** — MAINT-016 (medium) is the design-quality finding: `ChunkingTooLongForWorkerError` subclasses `InvalidLanguagePathError` for the type-tag dispatch pattern AGENTS.md prohibits (same as ARCH-018, cross-referenced). MAINT-017 (medium) finds the 13-line `chunks.json` parsing block byte-identical in `qwen3tts/handler.py:198-216` and `translategemma/handler.py:187-199` — direct parallel to MAINT-015 (inputs/artifacts structural copy) and MAINT-002 (redis/cache dual serialization). MAINT-018 (low) extends the duplication: per-chunk field validation diverges in shape (`_normalize_chunk` returns a `dict` vs qwen3tts's per-field accessors). MAINT-019 (low) finds `TranslateGemmaRunpodHandler.handle` is 54 lines (over 50) and bundles validation + parsing + inference + artifact-building — pattern-convergent with `Qwen3TTSRunpodHandler.handle`. TYPE-010 (low) bundles the third instance of the `self._model: Any = None` anti-pattern (translategemma = TYPE-009 second instance; qwen3tts gained a new `# type: ignore[no-any-return]` at line 172 as a knock-on). The 8c layer is the natural consolidation moment for `parse_chunks_json(input)` and a shared `_ModelProto`/`_ProcessorProto` Protocol.

- **CFG (B, 8 medium / 1 low)** — CFG-009 (medium) finds `Settings.chars_per_token` is a top-level knob consumed by exactly one function with the default `4` duplicated at the function signature (YAGNI per AGENTS.md). CFG-010 (medium) is the most operationally significant: `WorkerSettings.model_id` is now consumed correctly by translategemma (proving the wiring is feasible — 3-line change in 3 places), but qwen3tts and granite_speech still hard-code the value, widening CFG-007/008's silence from 4 YAMLs to 6. CFG-011 (low) extends the same pattern to `WorkerCapabilities.max_input_tokens` — 2 workers publish it as a hard-coded `2048` (not configurable via `WorkerSettings`).

- **TEST (A → B, 6 medium / 8 low)** — 4 new medium findings: TEST-014 (workers/translategemma/tests/test_handler.py does not cover the model.generate error path, partial-success, or pad_token_id init — 4 missing tests including a CUDA OOM propagation test and a pad_token_id init test), TEST-015 (`src/acheron/tls.py` has no direct unit tests — only 3 unblocked subprocess happy-path tests in `tests/integration/test_tls.py`; 8 missing unit tests on `_require_pair` / `uvicorn_ssl_kwargs` / `resolve_ca_path` / `grpc_server_credentials` / `grpc_channel`), TEST-016 (test_handler.py class-level mutation anti-pattern at line 235-241 — second instance of open TEST-012), TEST-017 (tests/integration/test_tls.py hardcodes 3 repo-relative paths via `Path(__file__).resolve().parents[2]` — direct AGENTS.md violation). DATA-009 (medium) finds `TestValidateChunkingFitsWorkers` has no boundary-condition test (==, one-over, `max_input_tokens=0`, empty caps). The grade falls from A to B because the medium count crosses the ≤2 medium threshold.

- **DOC (A → B, 3 medium / 1 low)** — DOC-005 (medium) is the greenfield-rule violation follow-on to ARCH-017: the `shell/tls.py` shim docstring is stale-prone with "live in :mod:`acheron.tls` now", "the helpers were moved", and "so existing import sites keep working" — all three references go stale once the old path is gone. DOC-006 (low) finds `submit_job` and `validate_chunking_fits_workers` have incomplete Google-style `Raises:` sections — `submit_job` lists `AcheronError` but not the new `ChunkingTooLongForWorkerError`, and `validate_chunking_fits_workers` has no `Raises:` section at all. The grade falls from A to B because the medium count crosses the ≤2 medium threshold.

No `stale` stories. No regressions of `fixed`/`verified` stories. 1 transition to `fixed` (MAINT-010). Grades: 7 themes at A (ML, MATH, TYPE, REPRO, DX, PKG, plus the empty ML/MATH buckets), 7 themes at B (ARCH, CFG, MAINT, EXC, TEST, DATA, PERF, OBS, DOC), 2 themes at C (CORR, SEC — SEC-008 critical; CORR newly crosses the 9-15 medium threshold). No aggregate codebase grade (per the rubric).

## Last orientation snapshot

**Repository**: acheron — audiobook processing pipeline (FastAPI orchestrator + gRPC/HTTP workers + Redis/memory stores). Greenfield (per AGENTS.md).

**Branch / HEAD**: `chore/code-review-update` at `eb6849c85d83f2277eb450f18a11e63cae2defd1`.

**Top-level layout**: `src/acheron/core/` (domain models, errors, chunking, planner, interfaces), `src/acheron/shell/` (orchestrator, API, executors: streaming/async/sequential, stores: memory/redis, transports: http/grpc/local, cache, health, TLS, step_handler, local_handlers, capabilities, health_providers, config), `src/acheron/worker_sdk/` (base SDK for building workers — config_loader, _edge_http, _runpod_client, registration, pricing, artifacts, cloud, handler, app, cli, settings, schemas, inputs), `src/acheron/tls.py` (NEW top-level — TLS helpers shared by shell + worker_sdk + workers), `dashboard/` (separate package), `stubs/` (7 generic SDK-backed stubs + _sdk_base + nltk mock), `workers/qwen3tts/` (RunPod serverless TTS worker, uv workspace member), `workers/granite_speech/` (RunPod serverless ASR worker, uv workspace member), `workers/translategemma/` (RunPod serverless translation worker, uv workspace member, NEW in 8c), `workers/_shared.py` + `workers/_shared/` (shared helpers — `safe_chapter_id`), `tests/` (mirrors src: tests/core, tests/shell, tests/worker_sdk, tests/integration, tests/scripts; plus stubs/tests/, workers/<pkg>/tests/).

**No hexagonal layers**: flat package structure. Interfaces (ABCs) in `core/interfaces.py`. No `ports.py` files.

**Boundaries** (enforced by import-linter): `core` must NOT import `shell`; `worker_sdk` must NOT import `shell`; `workers` must NOT import `shell`. The new `src/acheron/tls.py` (NEW in 8c) is at the top level so both `shell` and `worker_sdk`/`workers` can consume it without violating the import-linter contract.

**Test landscape**: `tests/core/`, `tests/shell/{api,stores,transports}`, `tests/worker_sdk/` (18 test files mirroring 14 source modules), `tests/integration/`, `tests/scripts/`. New since last review: full `workers/translategemma/tests/` (3 files: `test_capabilities.py` 154 lines, `test_handler.py` 269 lines, `test_runpod_entrypoint.py` 55 lines). Also new: `tests/core/test_planner.py` gained 106 lines (`TestValidateChunkingFitsWorkers` 9 tests), `tests/shell/test_http_worker.py` gained 174 lines, `tests/shell/transports/test_asr_multipart.py` grew 174 lines, `tests/integration/test_tls.py` grew 50 lines (now unblocks 3 previously xfailed TLS tests). Workspace root has a `conftest.py` for cross-workspace test discovery.

**Tooling**: `just certs install lint-imports lint-strict proto test type-check type-check-pyright validate`. All deps `~=` pinned. uv workspace members: `workers/{qwen3tts, granite_speech, translategemma, _shared}`. PKG-003 (cryptography pin drift) remains: `Dockerfile:39` pins `cryptography~=49.0` while root `pyproject.toml:168` pins `cryptography~=46.0`; not fixed in 8c.

**Key entry points**: `acheron.cli:main`, `acheron.worker_sdk.cli:main` (`acheron-worker-edge`), `acheron.shell.api.__main__`, `acheron.shell.api.app:create_app`. Worker runpod entrypoints: `workers/<pkg>/runpod_entrypoint:main`. Worker edge entrypoints: `worker_sdk/cli.py` (configurable via `WORKER_NAME` env var; expects `/app/<name>.worker.yaml`).

**Changes since last review** (delta brief): the diff `e544584..eb6849c` is 31 commits, 51 files, +7757/-510. New since last review: `workers/translategemma/` (full RunPod serverless translation worker with handler 315 lines, runpod_entrypoint 35 lines, Dockerfile.runpod 59 lines, pyproject, README 97 lines, worker.yaml + worker.edge.yaml, 3 test files), `src/acheron/tls.py` (NEW 114 lines, moved from `shell/tls.py` which is now a 24-line shim), `validate_chunking_fits_workers` (NEW in `core/planner.py:92-128` with the `ChunkingTooLongForWorkerError` exception in `core/errors.py:16-22`), `Settings.chars_per_token` (NEW in `shell/config.py:141`), `WorkerCapabilities.max_input_tokens` (NEW field in `core/models.py:89`), `HttpWorker.execute()` rewrite to `match job.job_type` with the shared `_execute_with_upstream_input` helper and new `_post_multipart` method, `submit_job` adds the `validate_chunking_fits_workers` call after `compile_plan`, `worker_sdk/cli.py` adds 10 lines for TLS, `workers/qwen3tts/handler.py` reads chunks from `Input` and publishes `max_input_tokens=2048`, `stubs/{tts_grpc_stub, tts_local_stub}/main.py` updated to pass uvicorn SSL kwargs (18 lines each), `docker-compose.yml` adds the `translategemma-edge` service under the `runpod-translation` profile (port 8009:8001), `.github/workflows/build-workers.yml` adds `build-translategemma`, `pyproject.toml` adds 6 lines for the new workspace member + ruff overrides + testpaths entry.

**Out-of-scope but worth flagging** (from the bundle agents' bundle_notes):
- Bundle E flagged that the new `src/acheron/tls.py` module's `cert_pem = Path(cert_path).read_bytes()` raises bare `FileNotFoundError` rather than a typed `AcheronError` — pattern inconsistency with the typed-error convention used elsewhere.
- Bundle E flagged that the new `shell/tls.py` shim is a 24-line re-export whose docstring is stale-prone; this was filed as DOC-005.
- Bundle B flagged that the translategemma handler hard-codes `_MAX_INPUT_TOKENS = 2048` instead of `WorkerSettings.max_input_tokens` — see CFG-011.
- Bundle C found the two new `try/except (json.JSONDecodeError, UnicodeDecodeError)` sites both chain correctly with `from exc` — the EXC bundle is anchored at B with no new anti-patterns.
- Bundle D noted that no cited code in any open story is gone; the new test files use tmp_path / monkeypatch correctly.
- Bundle F noted that 5 new env vars (`GRANITE_SPEECH_RUNPOD_ENDPOINT_ID`, `GRANITE_SPEECH_PRICE_SOURCE`, `TRANSLATEGEMMA_RUNPOD_ENDPOINT_ID`, `TRANSLATEGEMMA_PRICE_SOURCE`, `TRANSLATEGEMMA_MODEL_ID`, plus `ACHERON_ALLOW_INSECURE` and `ACHERON_CHARS_PER_TOKEN`) are not in the README Configuration table — DOC-003 widens.

**Hand off to `code-review-tackle` for newly-surfaced stories.** The most impactful 8c-delta items to tackle first (lowest effort, highest severity):
1. SEC-023 (high, S) — fix `Dockerfile.edge` to copy translategemma handler
2. SEC-022 (high, S) — drop the `:-dev-registration-token` fallback in `docker-compose.yml:242`
3. ARCH-017 (high, S) — delete `shell/tls.py` shim, migrate 7 import sites
4. ARCH-018 (high, S) — drop the `InvalidLanguagePathError` parent from `ChunkingTooLongForWorkerError`
5. CORR-026 (medium, S) — fix the CJK docstring claim and add a test
6. CORR-031 (low, S) — one-line syntax fix for `HttpWorker.health` `except` clause
