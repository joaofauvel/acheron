---
branch: chore/code-review-update
initial_review_commit: 23c29e1
last_updated_commit: e54458416e9bfe890a473dd9d542978d205b40a1
last_staleness_scan:
  commit: e54458416e9bfe890a473dd9d542978d205b40a1
  date: 2026-06-23
---

# Code Review Summary

## Per-theme grades

| Theme | Grade | Stories (open/in-progress/stale) |
|---|---|---|
| CORR | B | 0 critical, 1 high, 7 medium, 9 low |
| ML | A | 0 critical, 0 high, 0 medium, 0 low |
| MATH | A | 0 critical, 0 high, 0 medium, 0 low |
| ARCH | B | 0 critical, 0 high, 5 medium, 4 low |
| CFG | B | 0 critical, 0 high, 6 medium, 0 low |
| MAINT | B | 0 critical, 0 high, 5 medium, 7 low |
| EXC | B | 0 critical, 0 high, 3 medium, 1 low |
| TYPE | A | 0 critical, 0 high, 2 medium, 6 low |
| TEST | A | 0 critical, 0 high, 2 medium, 8 low |
| REPRO | A | 0 critical, 0 high, 1 medium, 1 low |
| DATA | B | 0 critical, 0 high, 3 medium, 1 low |
| PERF | B | 0 critical, 0 high, 4 medium, 1 low |
| OBS | B | 0 critical, 0 high, 5 medium, 2 low |
| SEC | C | 1 critical, 4 high, 3 medium, 7 low |
| DX | A | 0 critical, 0 high, 1 medium, 0 low |
| PKG | A | 0 critical, 0 high, 1 medium, 1 low |
| DOC | A | 0 critical, 0 high, 2 medium, 0 low |

## Top concerns

1. SEC-008 — Auto-generated registration token is logged in plaintext at startup [critical] — `operations.md`
2. CORR-014 — `RunPodClient.run` silently treats a FAILED RunPod job as a successful empty result [high] — `correctness.md`
3. SEC-007 — Host Path Traversal & Arbitrary Local File Read in ExtractionHandler [high] — `operations.md`
4. SEC-009 — Registration token file created with process umask (potentially world-readable) [high] — `operations.md`
5. SEC-011 — `ACHERON_REGISTRATION_TOKEN` defaults to publicly-known `dev-registration-token` in compose and `.env.example` [high] — `operations.md`
6. SEC-018 — `granite-speech-edge` compose service hardcodes `:-dev-registration-token` fallback (new instance of SEC-011) [high] — `operations.md`

## Quick wins

1. SEC-008 — Auto-generated registration token is logged in plaintext at startup [critical, S effort] — `operations.md`
2. CORR-014 — `RunPodClient.run` silently treats a FAILED RunPod job as a successful empty result [high, S effort] — `correctness.md`
3. SEC-009 — Registration token file created with process umask (potentially world-readable) [high, S effort] — `operations.md`
4. SEC-011 — `ACHERON_REGISTRATION_TOKEN` defaults to publicly-known `dev-registration-token` in compose and `.env.example` [high, S effort] — `operations.md`
5. SEC-018 — `granite-speech-edge` compose service hardcodes `:-dev-registration-token` fallback (new instance of SEC-011) [high, S effort] — `operations.md`
6. ARCH-009 — HealthProvider ABC lives in shell/health_providers.py instead of core/interfaces.py [medium, S effort] — `architecture.md`
7. ARCH-011 — `worker_sdk/__init__.py` docstring falsely claims the module is GPU-SDK-free at import time [medium, S effort] — `architecture.md`
8. ARCH-012 — `create_worker_app` cherry-picks routes from `EdgeApp.app.routes` via a hardcoded `inner_paths` set [medium, S effort] — `architecture.md`
9. ARCH-015 — `step_cache` is threaded through `default_worker_factory` even though only the HTTP branch consumes it [medium, S effort] — `architecture.md`
10. CFG-003 — `ACHERON_OPEN_REGISTRATION` read directly in deps.py, bypassing the new settings loader [medium, S effort] — `architecture.md`
11. CFG-004 — Orchestrator mutates `Settings.orchestrator.data_dir` in-place from two call sites [medium, S effort] — `architecture.md`
12. CFG-005 — `${VAR}` env-var expansion silently substitutes unset env vars as empty strings, disabling providers [medium, S effort] — `architecture.md`
13. CFG-006 — Env vars read outside the project's settings loaders — 5 new sites in transports and worker_sdk [medium, S effort] — `architecture.md`
14. CFG-007 — `WorkerSettings.model_id` and `WorkerSettings.output_mode` are config knobs that don't actually control anything [medium, S effort] — `architecture.md`
15. CFG-008 — CFG-007 regression: `WorkerSettings.model_id` set in 4 YAML files and still never consumed by any handler [medium, S effort] — `architecture.md`
16. CORR-009 — Step handler caches worker list and worker instances across steps and plans [medium, S effort] — `correctness.md`
17. CORR-010 — `${VAR}` env-var expansion silently substitutes missing variables with empty string [medium, S effort] — `correctness.md`
18. CORR-013 — `_parse_multipart` discards per-part `X-Acheron-Metadata` header sent by the SDK edge [medium, S effort] — `correctness.md`
19. CORR-015 — `create_worker_app` cherry-picks routes from `EdgeApp` via hardcoded `inner_paths`; new routes silently dropped [medium, S effort] — `correctness.md`
20. CORR-020 — `make_runpod_handler` silently coerces missing `data` field to empty bytes [medium, S effort] — `correctness.md`
21. DATA-005 — RedisWorkerStore._deserialize_worker invalid status field has no corruption test [medium, S effort] — `verification.md`
22. DATA-006 — `HttpWorker._parse_multipart` edge cases (no metrics part, missing boundary, non-multipart body) are not covered [medium, S effort] — `verification.md`
23. DATA-008 — `HttpWorker._parse_multipart` response-side edge cases still uncovered after Layer 8b test additions [medium, S effort] — `verification.md`
24. DOC-003 — Configuration docs drift across README, .env.example, and an undocumented dashboard env var [medium, S effort] — `surface.md`
25. DOC-004 — README architecture tree, CI section, and Test paths omit the new `granite_speech` worker [medium, S effort] — `surface.md`
26. DX-003 — `just install` does not install the new `workers/qwen3tts/` workspace member, breaking the documented fresh-clone setup [medium, S effort] — `surface.md`
27. EXC-004 — `create_worker_app` lifespan catches bare `BaseException` for the eager price refresh; swallows `KeyboardInterrupt`/`SystemExit`/`CancelledError` [medium, S effort] — `code-quality.md`
28. EXC-005 — `_edge_http.py` `_dispatch` catches bare `BaseException` for handler failures; same anti-pattern as EXC-004 in a second file [medium, S effort] — `code-quality.md`
29. MAINT-006 — Orchestrator.start() inlines 17-line registration-token block; logs the token in plaintext [medium, S effort] — `code-quality.md`
30. MAINT-007 — RunPodHealthProvider and HuggingFaceHealthProvider duplicate the HTTP fetch envelope [medium, S effort] — `code-quality.md`
31. OBS-005 — Health providers swallow `(httpx.HTTPError, OSError)` silently with no diagnostic log [medium, S effort] — `operations.md`
32. OBS-006 — `RunPodClient` and `RunPodPrice` swallow transport / API errors with no log line [medium, S effort] — `operations.md`
33. OBS-007 — Edge `/execute` endpoint is unauthenticated; `docker-compose` exposes it on host network (8004:8001) [medium, S effort] — `operations.md`
34. OBS-009 — `granite-speech-edge` service exposes `/execute` on host port 8008 — unauthenticated (new instance of OBS-007) [medium, S effort] — `operations.md`
35. PERF-004 — HealthMonitor._check_all processes worker results sequentially with W Redis round-trips [medium, S effort] — `operations.md`
36. PERF-005 — Provider status checks in _handle_failure run sequentially and can starve the health interval [medium, S effort] — `operations.md`
37. PERF-006 — Edge `/execute` buffers entire multipart body in memory; O(n²) append for FileArtifact streams [medium, S effort] — `operations.md`
38. PERF-007 — Per-call `httpx.AsyncClient` construction in health probes and pricing refresh (no connection reuse) [medium, S effort] — `operations.md`
39. PKG-003 — `Dockerfile:39` (certs-init stage) pins `cryptography~=49.0` while `pyproject.toml:168` pins `cryptography~=46.0` [medium, S effort] — `surface.md`
40. SEC-013 — `RunPodPrice` sends API key as URL query parameter instead of Authorization header [medium, S effort] — `operations.md`
41. SEC-014 — `worker.edge.yaml` default `orchestrator_url` is HTTP — registration token sent in cleartext when env var is not overridden [medium, S effort] — `operations.md`
42. SEC-016 — Granite-speech edge image default `orchestrator_url` is HTTP — registration token sent in cleartext (new instance of SEC-014) [medium, S effort] — `operations.md`
43. SEC-017 — Granite-speech runpod image runs as root — no `USER` directive (new instance of SEC-015) [low, S effort] — `operations.md`
44. SEC-019 — Edge `/execute` multipart branch returns 500 body with `error=str(exc)`, exposing raw exception detail (new instance of SEC-012) [low, S effort] — `operations.md`
45. PERF-008 — `HttpWorker._post_multipart` constructs a new `httpx.AsyncClient` per call (new instance of PERF-007) [low, S effort] — `operations.md`
46. TYPE-009 — `GraniteSpeechRunpodHandler` types `self._model` and `self._processor` as `Any`; 2-line comment is a stale-prone impl-phase justification [low, S effort] — `code-quality.md`

## Story counts

| Status | Count |
|---|---|
| open | 104 |
| in-progress | 0 |
| fixed | 1 |
| verified | 43 |
| stale | 0 |
| wontfix | 0 |

## Changes since last review

The diff `dbec2be..e544584` (36 commits, 52 files changed, 7012 insertions / 84 deletions) covers the close-out of Layer 8b: the granite-speech RunPod serverless worker (handler, runpod_entrypoint, Dockerfile.runpod, pyproject, README, tests, worker.yaml + worker.edge.yaml), the new `workers/_shared` workspace member with `safe_chapter_id`, the SDK `Input` Protocol (`BytesInput` / `StreamInput` / `FileInput`), the HTTP transport `_execute_asr_multipart` / `_post_multipart` fan-in, the `step_cache` thread-through, the GHCR publish workflow for the new image, the `granite-speech-edge` compose service under the `runpod-asr` profile, and the README Quick Start fix (`acheron job ...`).

28 new findings surfaced: 8 CORR, 3 ARCH, 1 CFG, 1 MAINT, 1 EXC, 1 TYPE, 5 TEST, 1 DATA, 4 SEC, 1 OBS, 1 PERF, 1 DOC. The pattern across 8b is "second instance" — most new findings are second-instances of pre-existing patterns (SEC-011, SEC-014, SEC-015, SEC-012, OBS-007, PERF-007, CORR-013) widened by the new granite_speech worker image and the new ASR transport path. One story (DX-002) transitioned to `fixed` in 5b55e6f. 56 carry-overs were re-resolved: 17 CORR (incl. CORR-009/010/011/012/013/014/015/016/017), 6 ARCH (ARCH-008/009/010/011/012/013), 5 CFG (CFG-003/004/005/006/007), 11 MAINT, 3 EXC, 7 TYPE, 5 TEST, 2 REPRO, 3 DATA, 2 PERF, 2 OBS, 6 SEC, 2 PKG, 1 DOC. No previously verified/fixed/wontfix stories regressed. No stories were marked `stale` (cited code still exists at re-resolved line numbers).

Dominant themes:

- **CORR (B, 1 high / 7 medium / 9 low)** — CORR-014 (high) is the most significant existing finding: `RunPodClient.run` never inspects `output.status`, so a FAILED RunPod job propagates as a successful empty `/execute` response. Layer 8b's input-payload validation gaps (CORR-020 silent empty `data`, CORR-021 no `input_audio`-is-dict check, CORR-022 no `content_type`-is-str check) form a cluster in `make_runpod_handler`; the fix is one `isinstance` check per branch. The new ASR path introduces two memory-cliff findings (CORR-018 orchestrator request side, CORR-019 SDK edge request side) that mirror the pre-existing CORR-017 response-side materialization — the round trip is now unbounded in both directions. CORR-013 (per-part `X-Acheron-Metadata` discard) is now widended to CORR-024 (request-side hardcoded `metadata={}`); both are now open in both directions. The runpod_edge_http edge branch (CORR-023/025) leaks JSONDecodeError/ValidationError as opaque 500s and treats any non-JSON multipart part as audio.

- **ARCH (B, 5 medium / 4 low)** — ARCH-014 (medium) is the central new finding: `HttpWorker.execute()` now branches on `WorkerType.ASR` and dispatches to `_execute_asr_multipart`, inverting the transport-neutral Worker boundary. ARCH-015 (medium) follows: `step_cache` is threaded through `default_worker_factory` even though only the HTTP branch consumes it, leaking the ASR-input concern into the dispatch signature. ARCH-016 (low) is structural: `workers/_shared` is a module file co-located with a same-name test directory and an out-of-workspace `pyproject.toml` — a latent package-vs-module footgun. The hexagonal layering is intact: `core` does not import `shell`, `worker_sdk` does not import `shell`, `workers` does not import `shell`.

- **SEC (C, 1 critical / 4 high / 3 medium / 7 low)** — grade unchanged at C (still driven by SEC-008 critical, the auto-generated token logged in plaintext at startup). The 8b work added 4 new SEC findings, all second-instances: SEC-016 (granite-speech edge yaml default `orchestrator_url: http://...` — same as SEC-014), SEC-017 (granite-speech Dockerfile.runpod has no `USER` directive — same as SEC-015), SEC-018 (granite-speech-edge compose service adds a 4th `${ACHERON_REGISTRATION_TOKEN:-dev-registration-token}` fallback — same as SEC-011), SEC-019 (the new multipart branch in `_edge_http.py` replicates the unfixed `error=str(exc)` anti-pattern of SEC-012). The dominant new risk is the dev-default bypass broadening from 1 compose service to 2 (SEC-018).

- **OBS (B, 5 medium / 2 low)** — OBS-009 (medium) is the most operationally significant new finding: the new `granite-speech-edge` compose service maps `8008:8001` on the host network, exposing the unauthenticated POST `/execute` endpoint that forwards to a RunPod serverless endpoint that bills the operator. Same pattern as OBS-007 (qwen3tts-edge on 8004:8001). The RunPod client + pricing module's silent error swallowing (OBS-006) widens with the new ASR path.

- **MAINT (B, 5 medium / 7 low)** — MAINT-015 (medium) is the central new finding: `inputs.py` (NEW 79 lines) is a near-verbatim structural copy of `artifacts.py` (78 lines) — same Protocol + three-variant shape duplicated 95%. A 4th copy is inevitable unless a shared `_wire.py` base is introduced. EXC-005 (medium) finds the `_edge_http._dispatch` `except BaseException` is now present in 2 files (`app.py:122` and `_edge_http.py:242`), and the original 3-line rationale comment was removed during the refactor. TYPE-009 (low) extends the `Any` proliferation pattern (TYPE-008) into the new worker packages via `GraniteSpeechRunpodHandler._model: Any`.

- **PERF (B, 4 medium / 1 low)** — PERF-008 (low) is the second-instance of PERF-007: the new `_post_multipart` method in `transports/http.py:143-165` opens a throwaway `httpx.AsyncClient` per call when `self._client is None` (the common case). The PERF-007 fix at the `default_worker_factory` seam would close both call sites at once.

- **DATA (B, 3 medium / 1 low)** — DATA-008 (medium) is the most significant re-resolution: the new `test_asr_multipart.py` covers the ASR REQUEST side (6 scenarios) but the response-side `_parse_multipart` defensive branches (no metrics part, missing boundary, non-multipart body) remain uncovered, widening the gap DATA-006 already tracked. Coverage report confirms `transports/http.py:181-182, 198, 217` are still uncovered.

- **TEST (A, 2 medium / 8 low)** — 5 new low-severity findings: TEST-009 (`test_inputs.py` missing Protocol isinstance + 3 edge cases), TEST-010 (no unicode chapter_id coverage), TEST-011 (no default-fallback assertions in `test_cloud_audio.py`), TEST-012 (module-level mutation instead of `monkeypatch.setattr`), TEST-013 (no `X-Acheron-Metadata` build-side assertion). The grade stays A because all 5 are low-severity patterns.

- **DX (A, 1 medium)** — DX-002 transitioned to `fixed` in 5b55e6f (README Quick Start `acheron submit` replaced with `acheron job ...`). DX-003 remains open and re-resolved: the new `workers/granite_speech` workspace member widens the `just install` gap.

- **DOC (A, 2 medium)** — DOC-004 (medium) is the new README onboarding drift: the architecture tree (line 76-77), test paths (line 125), and CI section (line 160-163) all omit the new `granite_speech` worker despite the new `build-granite-speech` GHCR job and the new `granite-speech-edge` compose service. DOC-003 widens by the same shape (new `GRANITE_SPEECH_RUNPOD_ENDPOINT_ID` env var in compose but not in `.env.example`).

No `stale` stories. No regressions of `fixed`/`verified` stories. Grades: 9 themes at A (ML, MATH, TYPE, TEST, REPRO, DX, PKG, DOC, and the empty ML/MATH buckets), 7 themes at B (CORR, ARCH, CFG, MAINT, EXC, DATA, PERF, OBS), 1 theme at C (SEC — driven by SEC-008 critical). No aggregate codebase grade (per the rubric).

## Last orientation snapshot

**Repository**: acheron — audiobook processing pipeline (FastAPI orchestrator + gRPC/HTTP workers + Redis/memory stores). Greenfield (per AGENTS.md).

**Branch / HEAD**: `chore/code-review-update` at `e54458416e9bfe890a473dd9d542978d205b40a1`.

**Top-level layout**: `src/acheron/core/` (domain models, errors, chunking, planner, interfaces), `src/acheron/shell/` (orchestrator, API, executors: streaming/async/sequential, stores: memory/redis, transports: http/grpc/local, cache, health, TLS, step_handler, local_handlers, capabilities, health_providers, config), `src/acheron/worker_sdk/` (base SDK for building workers — config_loader, _edge_http, _runpod_client, registration, pricing, artifacts, cloud, handler, app, cli, settings, schemas, inputs), `dashboard/` (separate package), `stubs/` (7 generic SDK-backed stubs + _sdk_base + nltk mock), `workers/qwen3tts/` (RunPod serverless TTS worker, uv workspace member), `workers/granite_speech/` (RunPod serverless ASR worker, uv workspace member, NEW in 8b), `workers/_shared.py` + `workers/_shared/` (shared helpers — `safe_chapter_id`, NEW in 8b), `tests/` (mirrors src: tests/core, tests/shell, tests/worker_sdk, tests/integration, tests/scripts; plus stubs/tests/, workers/<pkg>/tests/).

**No hexagonal layers**: flat package structure. Interfaces (ABCs) in `core/interfaces.py`. No `ports.py` files.

**Boundaries** (enforced by import-linter): `core` must NOT import `shell`; `worker_sdk` must NOT import `shell`; `workers` must NOT import `shell`.

**Test landscape**: tests/core/, tests/shell/ (api/, stores/, transports/), tests/worker_sdk/ (15 test files mirroring 13 source modules), tests/integration/, tests/scripts/. New since last review: full granite_speech test module (3 files: capabilities, handler, runpod_entrypoint), `workers/_shared/tests/test_safe_chapter_id.py` (11 edge cases), `tests/worker_sdk/test_cloud_audio.py` (146 lines), `test_edge_http_multipart.py` (162 lines), `test_inputs.py` (86 lines), `test_handler_signature.py` (62 lines), `tests/shell/transports/test_asr_multipart.py` (233 lines, 6 ASR scenarios), expanded step_handler tests, root `conftest.py` for cross-workspace test discovery.

**Tooling**: `just certs install lint-imports lint-strict proto test type-check type-check-pyright validate`. All deps `~=` pinned. uv workspace members: `workers/qwen3tts`, `workers/granite_speech`, `workers/_shared`.

**Key entry points**: `acheron.cli:main`, `acheron.worker_sdk.cli:main` (`acheron-worker-edge`), `acheron.shell.api.__main__`, `acheron.shell.api.app:create_app`.
