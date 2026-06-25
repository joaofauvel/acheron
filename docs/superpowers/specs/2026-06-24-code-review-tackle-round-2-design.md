---
topic: code-review-tackle-round-2
date: 2026-06-24
parent_design: 2026-06-24-code-review-tackle-bundles-design.md
round: 2 of N
branch: fix/code-review-tackle-2
worktree: .worktrees/code-review-tackle/
base_commit: bdd20e4
---

# Code Review Tackle — Round 2 Design

## 1. Goal

Tackle the 123 remaining open code-review stories from `docs/code_review/`, building on the 15 stories already fixed in Round 1 (see `2026-06-24-code-review-tackle-bundles-design.md`).

The remaining set contains:
- 1 HIGH-severity story (SEC-007, M effort)
- 27 M-effort stories (architectural refactors; need per-story design)
- ~95 S-effort stories (quick wins; bundle by cross-cutting concern)

## 2. Bundle design

**Ordering principle:** severity-first (HIGH → MEDIUM → LOW), then S-effort before M-effort within a severity, with cross-cutting concerns grouped so each bundle touches one logical area.

**Bundle size:** 3-6 stories typical; 5 bundles (B10, B12, B16, B19, B22) at 7-8 are accepted as overflows to keep cross-cutting concerns intact.

### Bundle table

| # | Bundle | Sev | Count | Cross-cutting concern |
|---|---|---|---|---|
| B1 | Host path traversal | HIGH | 1 | SEC-007: ExtractionHandler allowlist + `Path.resolve()` |
| B2 | HttpWorker multipart cleanup | MED | 6 | CORR-013/27/28/30, DATA-006/08: response parsing + dispatch |
| B3 | HttpWorker multipart edge cases | LOW | 6 | CORR-021/22/23/24/25, ARCH-022: input validation + dedup |
| B4 | Settings sprawl A — loaders + env vars | MED | 6 | CFG-003/04/05/06, CORR-010/11: project settings loaders |
| B5 | Settings sprawl B — model_id, output_mode | MED | 4 | CFG-007/08/10/11: WorkerSettings authority |
| B6 | Settings cleanup C — chars_per_token + factory | MED | 2 | CFG-009, ARCH-015: settle the top-level knob |
| B7 | Exception handling A — typed errors | MED | 6 | EXC-004/05, OBS-005/06/08, SEC-013: replace `BaseException` |
| B8 | Exception handling B — observability | LOW | 5 | EXC-003, OBS-003, SEC-006/10/12: log + sanitise |
| B9 | RunPod forwarder security (3rd-instance widening) | MIX | 5 | SEC-014/15/16/17, CORR-020: edge hardening |
| B10 | Health monitor & perf | MIX | 7 | PERF-004/05/07/08, OBS-001, TEST-007, CORR-012: monitoring |
| B11 | HttpWorker memory materialization | MIX | 4 | CORR-017/18/19, PERF-006: streaming I/O |
| B12 | Worker SDK consolidation | MED | 8 | ARCH-009/12/14/20, MAINT-011/13/15, CORR-015: single `EdgeApp` |
| B13 | Plan-time / chunking & OBS-011 | MIX | 3 | ARCH-019, OBS-011, DOC-006: fold check into `compile_plan` |
| B14 | Worker cleanup & TLS boilerplate | MED | 4 | ARCH-021, MAINT-017/18/19: shared helpers + handler size |
| B15 | TRANSLATEGEMMA handler refactor | LOW | 3 | CORR-029/32/33: streaming + partial-success |
| B16 | ARCH & type safety (low) | LOW | 7 | ARCH-008/10/13/16, CORR-009/16, TYPE-009: boundaries + docstrings |
| B17 | Type safety — typed models & ignores | MIX | 6 | TYPE-001/03/06/07/08/10: typed protocols |
| B18 | Type safety — stringly-typed responses | LOW | 3 | TYPE-004/05, MAINT-014: use enums |
| B19 | MAINT cleanup & Python 2 syntax | MIX | 8 | MAINT-002/05/06/07/08/09/12, CORR-031: modernisation |
| B20 | Tests — handler + edge coverage | MIX | 5 | TEST-005/06/14/15, DATA-007: unit-test gaps |
| B21 | Tests — orchestrator + step | MIX | 6 | TEST-002/09/10/11, REPRO-001/03: test quality |
| B22 | Tests — app + integration | MIX | 7 | TEST-008/12/13/16/17, DATA-005/09: app surface |
| B23 | DOC + DX + PKG | LOW | 6 | DOC-003/04, DX-003, PKG-002/03, EXC-001: docs & deps |
| B24 | Auth + remaining SEC + stale bookkeeping | LOW | 5 | SEC-005, SEC-019, SEC-011 (stale), OBS-007 (stale), OBS-009 (stale) |
| B25 | Final cleanup + summary.md refresh | — | 0 | re-grade themes, count updates, mark SEC-011/OBS-007/OBS-009 stale |

**Total: 121 stories + 3 stale-bookkeeping + final summary refresh.**

## 3. Per-bundle design

Each bundle has 1-line context + story list. M-effort stories get an inline 1-paragraph design (the only design-level content; S-effort is direct per the story text).

### B1 — Host path traversal (HIGH severity, 1 story, M effort)

**SEC-007** — `ExtractionHandler.extract_epub(ebook_path)` accepts a user-supplied path and passes it directly to `epub.read_epub` without bounds checking. An attacker who can submit jobs can read `/etc/passwd`, the orchestrator's secrets, or any file the orchestrator user can see. Fix: resolve the path, check it lives under a configured allowlist (e.g. `Settings.orchestrator.data_dir` + the per-job working dir), raise `AcheronError` (a new `PathNotAllowedError` if needed) with the attempted path on rejection. Test: 1 happy path (resolved path inside allowlist), 1 traversal attempt (`../etc/passwd`), 1 absolute-path attempt (`/etc/passwd`), 1 symlink-pointing-outside attempt.

### B2 — HttpWorker multipart cleanup (MED, 6 stories, all S effort)

- **CORR-013** — `_parse_multipart` discards per-part `X-Acheron-Metadata` header sent by the SDK. Fix: extract the header into `parsed.metadata: dict[str, str]`.
- **CORR-027** — `_execute_with_upstream_input` only POSTs the first matching file; multi-file outputs silently truncated. Fix: replace `next()` with a `match` on the upstream's `StepKind` (B12's ARCH-020 refactor is the structural fix; for now, raise `WorkerError` with a clear message when more than one file matches).
- **CORR-028** — `_parse_multipart` boundary extraction raises `IndexError` on response missing `boundary=`. Fix: guard the slice; raise `WorkerError` with the response Content-Type.
- **CORR-030** — `_parse_multipart` first `application/json` as metrics; sidecar JSON overwrites. Fix: pin the metrics part by name or by an `X-Acheron-Part-Name` header.
- **DATA-006** — `_parse_multipart` edge cases (no metrics, missing boundary, non-utf8). Add 3 direct unit tests covering the new error paths.
- **DATA-008** — `_parse_multipart` response-side edge cases (no metrics part, missing boundary, malformed body). Add 3 direct unit tests.

### B3 — HttpWorker multipart edge cases (LOW, 6 stories, all S effort)

- **CORR-021** — `make_runpod_handler` doesn't validate `input_audio` is bytes. Fix: `isinstance(payload["input_audio"], (bytes, bytearray))` raise `WorkerError` otherwise.
- **CORR-022** — `make_runpod_handler` doesn't validate `content_type` is a string. Fix: same pattern, raise `WorkerError` with the actual type.
- **CORR-023** — `_run_execute_multipart` only catches `WorkerError` from parser; `JSONDecodeError` and `KeyError` propagate. Fix: catch `(WorkerError, ValueError, KeyError)` and re-raise as `WorkerError`.
- **CORR-024** — Edge `_parse_multipart_request` hardcodes `BytesInput.metadata={}`; per-part metadata is lost. Fix: propagate the per-part metadata.
- **CORR-025** — Edge `_parse_multipart_request` treats any non-JSON part as audio regardless of filename/content-type. Fix: check the part's `Content-Type` starts with `audio/`; raise on mismatch.
- **ARCH-022** — `HttpWorker._post_multipart` is a near-byte-duplicate of `HttpWorker._request`. Fix: make `_post_multipart` a one-line wrapper around `_request` (parametrise the method override).

### B4 — Settings sprawl A — loaders + env vars (MED, 6 stories, all S effort)

- **CFG-003** — `ACHERON_OPEN_REGISTRATION` read in `deps.py` bypassing `Settings`. Fix: add to `Settings` (or `OrchestratorSettings`), read via the loader.
- **CFG-004** — Orchestrator mutates `Settings.orchestrator.data_dir` in-place from 2 call sites. Fix: each call site sets its own `Settings` (or a one-shot override context) instead of mutating.
- **CFG-005** — `${VAR}` env-var expansion silently substitutes unset vars as empty strings. Fix: raise on unset (use `:-` default or Python's `${VAR:?error}` semantics).
- **CFG-006** — Env vars read outside the project's settings loaders — 5 new sites in transports and worker_sdk. Fix: add the missing fields to `Settings`/`WorkerSettings`, remove the `os.environ.get` calls.
- **CORR-010** — `${VAR}` env-var expansion silently substitutes missing variables with empty string. (Same root cause as CFG-005.) Fix is the same.
- **CORR-011** — Env-var expansion pattern only matches uppercase variable names. Fix: accept `[A-Za-z_][A-Za-z0-9_]*`.

### B5 — Settings sprawl B — model_id, output_mode (MED, 4 stories, all S effort)

- **CFG-007** — `WorkerSettings.model_id` and `output_mode` are config knobs that don't control anything. Fix: either wire them up in qwen3tts/granite_speech or remove the fields and the corresponding YAML.
- **CFG-008** — `WorkerSettings.model_id` set in 4 YAML files but still not read in code. (Follow-on of CFG-007.) Fix: read it (CFG-010) or remove it.
- **CFG-010** — `WorkerSettings.model_id` consumed only by `translategemma`; qwen3tts and granite_speech still hard-code. Fix: 3-line change per worker to read `self._settings.model_id`.
- **CFG-011** — `WorkerCapabilities.max_input_tokens` published by 2 workers as hard-coded `2048`. Fix: source it from `WorkerSettings.max_input_tokens` (or a per-worker default); the orchestrator's `validate_chunking_fits_workers` already reads from the published capability.

### B6 — Settings cleanup C — chars_per_token + factory (MED, 2 stories, all S effort)

- **CFG-009** — `Settings.chars_per_token` is a top-level knob consumed by exactly one function with the default `4` duplicated at the function signature. Fix: drop the function-level default; the only callsite is the orchestrator which already passes `Settings.chars_per_token`.
- **ARCH-015** — `step_cache` threaded through `default_worker_factory` even though only the HTTP worker uses it. Fix: remove from the factory signature; the HTTP worker can take it from the orchestrator or from a closure in `Orchestrator._build_http_workers`.

### B7 — Exception handling A — typed errors (MED, 6 stories, all S effort)

- **EXC-004** — `create_worker_app` lifespan catches bare `BaseException` for the eager price refresh. Fix: narrow to `(httpx.HTTPError, OSError, ValueError)` and log `logger.exception(...)`.
- **EXC-005** — `_edge_http._dispatch` catches bare `BaseException` for handler failures. Fix: narrow to `Exception` (or the specific handler-error set), let `BaseExceptionGroup` and `KeyboardInterrupt` propagate.
- **OBS-005** — Health providers swallow `(httpx.HTTPError, OSError)` silently with no diagnostic. Fix: `logger.warning(...)` with `provider_name`, `endpoint`, `exc_class` before returning the cached/safe value.
- **OBS-006** — `RunPodClient` and `RunPodPrice` swallow transport / API errors with no log line. Fix: log `logger.warning(...)` with `endpoint_id`, `exc_class`, and (for pricing) the HTTP status. Already partially fixed for `RunPodClient` in Round 1's CORR-014 (only the FAILED-status path got a `WorkerError`; the to_thread exception sites still need a log).
- **OBS-008** — `create_worker_app` lifespan catches `BaseException` around price refresh (overlaps EXC-004). Fix is the same as EXC-004.
- **SEC-013** — `RunPodPrice` sends API key as URL query parameter. Fix: move to `Authorization: Bearer` header; rotate any leaked key.

### B8 — Exception handling B — observability (LOW, 5 stories, all S effort)

- **EXC-003** — `HealthMonitor._handle_failure` catches bare `Exception` from the platform probe. Fix: narrow + log.
- **OBS-003** — Logs are free-form with no structured fields or trace correlation. Fix: introduce a `job_id`/`request_id` contextvar + `structlog`-style bound loggers (or simple `%({job_id})s` format if `structlog` is rejected by AGENTS.md YAGNI).
- **SEC-006** — Raw exception strings exposed in `PlanResult.errors` via OBS-004 fix. Fix: sanitise to `{exc_class}: {first_line}` (drop traceback fragments).
- **SEC-010** — Worker `last_error` exposed via unauthenticated `/workers` endpoint. Fix: add an auth dependency (or scrub the field from the response when no auth).
- **SEC-012** — Edge `/execute` returns raw `str(exc)` in 500 body. Fix: return `{exc_class}: {sanitised_msg}` with a structured log line for the operator.

### B9 — RunPod forwarder security (3rd-instance widening) (MIX, 5 stories, all S effort)

- **SEC-014** — `worker.edge.yaml` default `orchestrator_url` is HTTP. Fix: default to HTTPS; document the dev override.
- **SEC-015** — All Docker images run as root. Fix: add `RUN useradd acheron && USER acheron` to each Dockerfile.
- **SEC-016** — Granite-speech edge image default `orchestrator_url` is HTTP. Fix is the same as SEC-014.
- **SEC-017** — Granite-speech runpod image runs as root. Fix is the same as SEC-015.
- **CORR-020** — `make_runpod_handler` silently coerces missing `data` field to empty bytes. Fix: raise `WorkerError` with the missing key.

### B10 — Health monitor & perf (MIX, 7 stories, 2 M effort)

- **PERF-004** — `HealthMonitor._check_all` processes worker results sequentially with W Redis round-trips. Fix: `asyncio.gather(...)` + pipelined `MGET`/pipeline writes; the new test asserts that a 10-worker health check completes in <N ms (calibrate to current `asyncio.to_thread` overhead).
- **PERF-005** — Provider status checks in `_handle_failure` run sequentially and can starve the loop. Fix: same gather + a small semaphore to bound concurrency.
- **PERF-007** — Per-call `httpx.AsyncClient` in health probes and pricing refresh. Fix: single module-level `AsyncClient` initialised in the provider's `__init__`, closed in a `close()` method called from the orchestrator's shutdown.
- **PERF-008** — `HttpWorker._post_multipart` constructs new `httpx.AsyncClient` per call. Fix: same pattern as PERF-007 (per-worker client).
- **OBS-001** (M) — Shutdown does not drain in-flight `_execute` tasks; cancelled jobs stay stuck at "running". Design: add `_in_flight: set[asyncio.Task]` to the orchestrator; in `stop()`, `await asyncio.gather(*_in_flight, return_exceptions=True)` with a 5s timeout, then cancel anything remaining. Test: assert that a slow `_execute` task finishes or is cancelled on `stop()`, and the `JobStatus` is reconciled.
- **TEST-007** (M) — `HealthMonitor._handle_failure` BOOTING→OFFLINE and OFFLINE→HEALTHY transitions untested. Design: add a `TestHealthMonitorTransitions` class with 4 tests (one per transition), all using the in-memory store and a fake provider that flips status on demand.
- **CORR-012** (M) — Health monitor trusts provider BOOTING status without bounding duration. Design: track `booting_since: float | None` per worker; on `BOOTING > 60s` (configurable via `Settings.health.boot_grace_seconds`), force OFFLINE. Test: provider that returns BOOTING forever → after grace period, status flips to OFFLINE.

### B11 — HttpWorker memory materialization (MIX, 4 stories, 3 M effort)

- **CORR-017** (M) — `_build_multipart_response` materializes the entire artifact stream in memory. Design: stream `FileArtifact` chunks (64 KiB) into the multipart writer as they're read; never accumulate the full body. Test: assert peak memory < (artifact size / 2) by reading the response in chunks and never holding the full body.
- **CORR-018** (M) — ASR multipart path materializes entire audio file. Design: same streaming pattern as CORR-017 for the request side; the `request.stream()` is already async — wrap and forward chunks.
- **CORR-019** (M) — SDK edge `_parse_multipart_request` materializes entire request body. Design: use `python-multipart`'s streaming parser (`MultipartConsumer` or equivalent) that yields parts one at a time; never call `await request.body()`. Test: assert that a 100MB request is processed without ever holding the full body.
- **PERF-006** — Edge `/execute` buffers entire multipart body in memory; O(n²) append for `FileArtifact`. Fix: stream-write each `FileArtifact` part to disk as it's parsed, never accumulate.

### B12 — Worker SDK consolidation (MED, 8 stories, 4 M effort)

- **ARCH-009** — `HealthProvider` ABC lives in `shell/health_providers.py` instead of `core/interfaces`. Design: move the ABC to `core/interfaces.py`; `shell/health_providers.py` re-exports it for back-compat. Test: assert the import path is `acheron.core.interfaces.HealthProvider`.
- **ARCH-012** — `create_worker_app` cherry-picks routes from `EdgeApp.app.routes` via hardcoded `inner_paths`. Design: use `app.mount("", EdgeApp.app)` (FastAPI sub-app) instead of copying routes.
- **ARCH-014** (M) — `HttpWorker.execute()` branches on `WorkerType.ASR` to add a transport-specific (multipart) flow. Design: replace the inline `if worker_type == ASR` with a `match` on the step's `StepKind`; the ASR branch becomes a registered `ASRStepHandler` and the rest stay in the existing `match` arms.
- **ARCH-020** (M) — `HttpWorker._execute_with_upstream_input` has a leaky triple-magic-string signature (`upstream_step`, `content_type_predicate`, `form_field`). Design: introduce a `StepDispatch` dataclass per (WorkerType, content_type) pair that bundles all three; the method becomes a generic loop over a `MATCHES_BY_TYPE` map.
- **MAINT-011** (M) — `create_worker_app` builds an `EdgeApp` only to copy its routes onto the outer app. Same fix as ARCH-012 (mount, don't copy).
- **MAINT-013** — `_caps_to_response` (edge) and `_caps_to_dict` (registration) duplicate. Fix: collapse to a single helper at `worker_sdk/_caps.py`.
- **MAINT-015** (M) — `inputs.py` is a near-verbatim copy of `artifacts.py`. Design: extract the shared Protocol into `worker_sdk/_io.py`; `inputs.py` and `artifacts.py` each import from it and add their domain-specific helpers.
- **CORR-015** — `create_worker_app` cherry-picks routes via hardcoded `inner_paths` (correctness angle, same as ARCH-012). Same fix.

### B13 — Plan-time / chunking & OBS-011 (MIX, 3 stories, all S effort)

- **ARCH-019** — `validate_chunking_fits_workers` is a post-step in `submit_job` that should be folded into `compile_plan`. Fix: call it from `compile_plan` (or its caller) and remove the post-step. Update the test that exercises the post-step.
- **OBS-011** — `validate_chunking_fits_workers` runs in `submit_job` with no log on success or failure. Fix: `logger.debug(...)` on the success path (with the max estimated tokens) and `logger.warning(...)` on the error (with the full error message — this is a guard rail, not a security boundary).
- **DOC-006** — `submit_job` and `validate_chunking_fits_workers` have incomplete Google-style `Raises:` sections. Fix: add `ChunkingTooLongForWorkerError` to `submit_job`'s `Raises:`; add a full `Raises:` section to `validate_chunking_fits_workers` (ValueError, ChunkingTooLongForWorkerError).

### B14 — Worker cleanup & TLS boilerplate (MED, 4 stories, all S effort)

- **ARCH-021** — Identical uvicorn+TLS 7-line boilerplate duplicated across 4 entry points. Fix: extract `run_worker_server(app: FastAPI, host: str, port: int, ssl_ctx: ssl.SSLContext | None)` to `worker_sdk/_server.py`.
- **MAINT-017** — `chunks.json` parsing duplicated byte-for-byte between qwen3tts and translategemma handlers. Fix: extract `parse_chunks_json(input: BytesInput) -> list[Chunk]` to `workers/_shared/chunks.py`.
- **MAINT-018** — Per-chunk field validation duplicated between translategemma and qwen3tts. Fix: shared `Chunk` dataclass + `validate_chunk_fields(chunk) -> None` in `workers/_shared/chunks.py`.
- **MAINT-019** — `TranslateGemmaRunpodHandler.handle` is 54 lines; bundles 3 distinct concerns. Fix: split into `_validate_payload`, `_parse_chunks`, `_translate_and_artifact`; the entry point orchestrates.

### B15 — TRANSLATEGEMMA handler refactor (LOW, 3 stories, all M effort)

- **CORR-029** (M) — `_translate_batch` has no partial-success handling; mid-batch failure discards all completed work. Design: wrap each chunk's translate call in `try/except (torch.cuda.OutOfMemoryError, ValueError)`; on per-chunk failure, log the chunk id + error, return the successful chunks + a `failed: list[ChunkRef]` in the result. The handler aggregates and raises `WorkerError` only when the success rate is < 50% (configurable threshold).
- **CORR-032** (M) — `handle` materializes the entire `chunks.json` in memory. Design: stream-parse the chunks.json input (B14's `parse_chunks_json` already iterates) and translate one chunk at a time. Test: assert peak memory < (chunks.json size / 2).
- **CORR-033** (M) — `_translate_batch` mutates the shared processor's tokenizer in-place. Design: deep-copy the tokenizer per call (or use a `with processor.as_target_tokenizer(...)` if HF's API supports it). Test: assert that calling `handle()` twice in sequence does not affect the second call's input shape.

### B16 — ARCH & type safety (LOW, 7 stories, all S effort)

- **ARCH-008** — `Orchestrator.__init__` derives default `StepCache` from `PlanCache.dir` coupling. Fix: take `step_cache` as an explicit parameter; default to `InMemoryStepCache()`.
- **ARCH-010** — `HealthProviders` is a no-behavior wrapper over `dict`. Fix: drop the wrapper, use `dict[str, HealthProvider]` directly.
- **ARCH-013** — `transports/grpc.py` and `transports/http.py` both duplicate the `data_dir` lookup. Fix: pass `data_dir` from the orchestrator to each transport's constructor.
- **ARCH-016** — `workers/_shared` is a module (file) co-located with a same-name test dir. Fix: rename to `workers/_shared_utils.py` (the dir is a real package) OR rename the test dir to `tests/_workers_shared/`.
- **CORR-009** — Step handler caches worker list and worker instances across steps and plans. Fix: invalidate the cache on `submit_job` and `cancel_job`.
- **CORR-016** — `worker_sdk` package docstring falsely claims GPU-SDK-free at import (overlap with ARCH-011). Fix: same fix as ARCH-011.
- **TYPE-009** — `GraniteSpeechRunpodHandler` types `self._model` and `self._processor` as `Any`. Fix: introduce a `_ModelProto`/`_ProcessorProto` Protocol (B17's TYPE-010 design) and type-annotate.

### B17 — Type safety — typed models & ignores (MIX, 6 stories, 4 M effort)

- **TYPE-001** (M) — `AcheronClient` returns `dict[str, Any]` consumed via magic-string keys. Design: introduce typed response models (`JobResponse`, `CapabilitiesResponse`, `WorkerResponse`) with Pydantic; `AcheronClient` returns them directly. Test: type-check + 1 round-trip test.
- **TYPE-003** (M) — `redis.py` accumulates 8 `# type: ignore[misc]` markers on `await self._redis.<method>`. Design: wrap the `redis.asyncio.Redis` in a thin typed proxy `RedisLike` Protocol and have `RedisStore` declare `self._redis: RedisLike` (not `Any`).
- **TYPE-006** (low) — `grpc.py` accumulates 5 `# type: ignore` markers for the new proto `Artifact` types. Fix: add a minimal type stub under `stubs/grpc_gen/` (or use `grpc-stubs` from pypi if available — but YAGNI: just declare the proto types in a local `.pyi`).
- **TYPE-007** (low) — `RunPodForwarderHandler.__init__` calls `phantom_handler(settings)` under `# type: ignore`. Fix: type the `phantom_handler` factory return value as a `RunPodHandlerProtocol`.
- **TYPE-008** (low, M) — WorkerSDK has 14+ `Any`/`dict[str, Any]` annotations in 5 files. Design: introduce a single `_JsonDict` alias and a `WorkerResponsePayload` discriminated union; replace `dict[str, Any]` annotations with the union where the keys are known.
- **TYPE-010** (low, M) — 3 RunPod worker handlers type `self._model`/`self._processor` as `Any`. Design: shared `_ModelProto`/`_ProcessorProto` Protocols in `workers/_shared/protocols.py`; each handler declares the concrete type or `Self[_ModelT]`.

### B18 — Type safety — stringly-typed responses (LOW, 3 stories, all S effort)

- **TYPE-004** — `WorkerResponse.status` is stringly-typed despite a `WorkerStatus` enum existing. Fix: replace `str` with `WorkerStatus`.
- **TYPE-005** — `JobResponse.status` and `total_cost_basis` are stringly-typed. Fix: replace `status` with `JobStatus` enum; keep `total_cost_basis` as a `Decimal` (Pydantic-friendly).
- **MAINT-014** — Stub handlers redundantly override the ABC's default no-op `startup`/`shutdown`. Fix: delete the 6 overrides.

### B19 — MAINT cleanup & Python 2 syntax (MIX, 8 stories, 1 M effort)

- **MAINT-002** (M) — `redis.py` hand-rolls JSON ser/deser for domain models that `cache.py` serializes via Pydantic. Design: extract a `serialize(obj: BaseModel) -> bytes` / `deserialize(model: type[T], blob: bytes) -> T` pair in `shell/serialization.py`; both stores use it.
- **MAINT-005** — `Orchestrator._execute` duplicates `PlanResult` construction across adjacent branches. Fix: extract `_build_plan_result(stage_outputs) -> PlanResult` and call from each branch.
- **MAINT-006** — `Orchestrator.start()` inlines 17-line registration-token block. Fix: extract to `_resolve_registration_token(settings, token_file) -> str` (already in Round 1's SEC-011 helper, but it's a method — pull it out as a free function).
- **MAINT-007** — `RunPodHealthProvider` and `HuggingFaceHealthProvider` duplicate the HTTP fetch envelope. Fix: extract `_async_fetch(url, *, headers=None, timeout) -> tuple[int, bytes]` to a shared base class or helper.
- **MAINT-008** — `HealthMonitor._handle_failure` reassigns its `error` parameter inside the function. Fix: rename the local to `_error` and use a new variable for the (optional) wrapped error.
- **MAINT-009** — Python 2-style `except A, B:` syntax used at 7 sites across 6 files. Fix: `except A as B:` (one-line regex replacement).
- **MAINT-012** — `_registration_caps` manually re-lists every `WorkerCapabilities` field. Fix: `WorkerCapabilities.model_dump(mode="json")`.
- **CORR-031** — `HttpWorker.health` uses deprecated Python 2 `except E1, E2:` syntax. Same fix as MAINT-009.

### B20 — Tests — handler + edge coverage (MIX, 5 stories, 2 M effort)

- **TEST-005** (S) — `_metadata_str` helper in `health.py` has no direct unit tests. Fix: add 3 tests (empty, single pair, multi-pair).
- **TEST-006** (S) — `HuggingFaceHealthProvider.check_status` has untested `str` and `else` branches. Fix: add 2 tests.
- **TEST-014** (M) — `workers/translategemma/tests/test_handler.py` does not cover the model.generate error path, partial-success, or pad_token_id init. Design: add 4 tests: (1) CUDA OOM propagation, (2) per-chunk ValueError → partial-success, (3) pad_token_id init when tokenizer has no pad_token, (4) empty input → graceful no-op.
- **TEST-015** (M) — `src/acheron/tls.py` (114 lines) has no direct unit tests. Design: 8 unit tests on `_require_pair` / `uvicorn_ssl_kwargs` / `resolve_ca_path` / `grpc_server_credentials` / `grpc_channel`; use the existing test certs and a tmp_path for missing-file paths.
- **DATA-007** (S) — `_runpod_client` output.artifacts-not-list path and `FileArtifact` stream edge cases lack direct tests. Fix: 2 tests for the artifacts-not-list path (round 1 added 2 for the FAILED/CANCELLED paths), 3 for `FileArtifact.stream` (empty file, 1-byte file, missing path).

### B21 — Tests — orchestrator + step (MIX, 6 stories, 2 M effort)

- **TEST-002** (M) — `test_orchestrator_works_with_redis_backend` tests memory, not Redis. Design: actually start a `fakeredis` (or local redis-server) instance in a fixture; assert that the test fails when redis is unreachable. The misnamed test is a future-trap.
- **TEST-009** (S) — `test_inputs.py` missing Protocol isinstance, FileInput missing-path, etc. Fix: 4 tests.
- **TEST-010** (S) — `test_safe_chapter_id.py` missing unicode `chapter_id` coverage. Fix: 3 tests (CJK, accented, emoji).
- **TEST-011** (S) — `test_cloud_audio.py` missing default-content_type and default-metadata tests. Fix: 2 tests.
- **REPRO-001** (M) — `Redis.list_all()` returns non-deterministic order — step_handler worker selection is non-deterministic. Design: sort by `worker_id` (or `last_seen` descending) in `list_all`; assert determinism in a 100-iteration test.
- **REPRO-003** (S) — `tests/worker_sdk/conftest.py` `_no_sleep` fixture masks `asyncio.sleep` globally. Fix: narrow to monkeypatch only the called module's `asyncio.sleep`, not the global.

### B22 — Tests — app + integration (MIX, 7 stories, all S effort)

- **TEST-008** (S) — `worker_sdk/app._build_price_source` static/runpod-missing-key branches untested. Fix: 2 tests.
- **TEST-012** (S) — `test_step_handler.py` mutates module-level `default_worker_factory` in a test. Fix: move the override to a fixture with `monkeypatch.setattr(..., autospec=True)`; the test should clean up automatically.
- **TEST-013** (S) — `test_edge_http.py` and `test_edge_http_multipart.py` don't assert `X-Acheron-Metadata` propagation. Fix: 2 tests asserting round-trip metadata.
- **TEST-016** (S) — `workers/translategemma/tests/test_handler.py:235-241` class-level mutation anti-pattern. Fix: refactor to fixture-based setup.
- **TEST-017** (S) — `tests/integration/test_tls.py` hardcodes 3 repo-relative paths via `Path(__file__).resolve().parents[2]`. Fix: use a `conftest.py`-provided fixture `repo_root: Path`.
- **DATA-005** (S) — `RedisWorkerStore._deserialize_worker` invalid status field has no corruption test. Fix: 2 tests (missing status, invalid status string).
- **DATA-009** (S) — `TestValidateChunkingFitsWorkers` has no boundary-condition test. Fix: 4 tests (`==` boundary, one-over, `max_input_tokens=0` ignored, empty caps).

### B23 — DOC + DX + PKG (LOW, 6 stories, 1 M effort)

- **DOC-003** (S) — Configuration docs drift across README, `.env.example`, and an undocumented dashboard. Fix: introduce a `docs/configuration.md` that lists every env var, its default, and where it's read; rewrite README + `.env.example` to defer to it.
- **DOC-004** (S) — README architecture tree, CI section, and Test paths omit `granite_speech`. Fix: add it.
- **DX-003** (S) — `just install` doesn't install the new `workers/qwen3tts/` workspace member. Fix: add `uv sync --all-packages` (or similar) to the install recipe.
- **PKG-002** (S) — `pyproject.toml` dead `root_package` key + duplicate `soundfile` dev entry. Fix: remove.
- **PKG-003** (S) — `Dockerfile:39` pins `cryptography~=49.0` while `pyproject.toml:168` pins `~=46.0`. Fix: align both to the newer pin and bump.
- **EXC-001** (M) — `tenacity` dependency is unused; `WorkerTimeoutError`/`PlanValidationError` are never raised. Design: either remove `tenacity` and the never-raised exceptions, or wire them up. Recommendation: remove (the existing `asyncio.wait_for` + `WorkerError` covers all the use cases).

### B24 — Auth + remaining SEC + stale bookkeeping (LOW, 5 stories, 1 M effort)

- **SEC-005** (M) — Job submission/listing/capabilities routes require no authentication. Design: introduce a `Settings.api.auth_token: str | None`; the FastAPI app adds a `Depends(verify_token)` to the write routes. Document the dev-mode override (`ACHERON_OPEN_REGISTRATION` already exists; this is its read-side analog).
- **SEC-019** (S) — Edge `/execute` multipart branch returns 500 body with `error=str(exc)`. Same fix as SEC-012.
- **SEC-011** — bookkeeping: status field should be `stale` (was fixed in Round 1, never updated).
- **OBS-007** — bookkeeping: status field should be `stale` (was fixed in Round 1, never updated).
- **OBS-009** — bookkeeping: status field should be `stale` (was fixed in Round 1, never updated).

### B25 — Final cleanup + summary.md refresh

- Update `docs/code_review/summary.md`: counts, grades, top concerns, quick-wins list.
- Re-run `code-review-update` is **out of scope** for Round 2 (call out for Round 3).
- Audit each touched file's `last_verified_at` for stories that share the file but weren't in any bundle.

## 4. Workflow

### Branch & worktree

- **Worktree**: reuse `.worktrees/code-review-tackle/` (currently at `bdd20e4 == master`).
- **Branch**: new `fix/code-review-tackle-2` (rebased onto `master @ bdd20e4`).
- After Round 2 is complete: FF-merge into master, push to both remotes (codeberg + github).

### Per-story workflow

For every story in every bundle, follow the existing `code-review-tackle` skill:

1. **TDD** (for behavior changes): write a failing test, then implement the fix.
2. **Verify gate**: `just validate` must pass (lint-strict + type-check + test).
3. **Correctness pass**: dispatch a fresh-context subagent with the story + diff; require verdict `addressed` (or `partial` with user approval).
4. **Doc-staleness pass**: dispatch a fresh-context subagent to update line ranges of other stories citing the touched files; apply the `still-present`/`mark-stale` actions.
5. **Atomic commit**: story code + review updates in one `fix(<STORY-ID>): <description>` commit (or one commit for a multi-story bundle of cross-cutting fixes).
6. **Advance status**: `open` → `fixed` (per commit) → `verified` (per bundle, when all stories in the bundle are green).

For S-effort stories without behavior change (pure refactor / doc), skip step 1 and start at step 2 (just rerun the verify gate).

### Per-bundle workflow

- Bundles land as **one or more commits** (typically 1 per story; cross-cutting fixes that share a code path land as 1 commit with multiple `fix(STORY-A, STORY-B)` IDs).
- Each bundle's review-updates commit is the **last** commit of the bundle.
- Between bundles, the user reviews + optionally pushes/PRs (default: no push; user decides).

### Per-round workflow

- All 25 bundles land on `fix/code-review-tackle-2`.
- Bundle 25 updates `summary.md`; user verifies the final tally.
- User FF-merges `fix/code-review-tackle-2` into `master` and pushes.

### Cross-cutting concerns

- **No `--pr` push** in this round (user drives the PR).
- **No amending** commits; if a story needs follow-up, land it as a new commit.
- **No skipping subagent passes** even for "obvious" fixes.
- **No bundling across concerns** to keep review surface small.

## 5. Out of scope (intentionally not tackled)

- **Stale-bookkeeping items** (SEC-011, OBS-007, OBS-009) — landed in B24 as 1-line YAML updates; the rest of the bookkeeping happens in B25.
- **`code-review-update` rerun** (the workflow that generates new findings) — explicitly deferred to Round 3 after Round 2 lands.
- **Dashboard** — no open dashboard stories; the 2 new settings knobs (auth_token, OBS-001's drain) may need dashboard updates — call out for Round 3.
- **New layers / new features** — Round 2 is code-review-only, no new functionality.
- **Refactor of `docs/code_review/` review files themselves** — those files are append-only; B25 updates the summary.

## 6. Risks & known unknowns

### Likely to surface new work mid-bundle

- **B19** (MAINT cleanup) is the largest bundle (8 stories, 6+ files). The Python 2 `except` syntax fix (MAINT-009 + CORR-031) is mechanical, but `MAINT-002` (redis JSON ser/deser) and `MAINT-006` (token block) are non-trivial. **Mitigation**: tackle MAINT-005, -008, -009, -012, CORR-031 first (mechanical); leave MAINT-002, -006, -007 for last in the bundle.
- **B10** (Health monitor & perf) has 3 M-effort stories (OBS-001, TEST-007, CORR-012) that interact. **Mitigation**: tackle OBS-001 first (drain), then TEST-007 + CORR-012 (transitions), then PERF-* (concurrency).
- **B11** (Memory materialization) has 3 M-effort stories (CORR-017, -018, -019) all of which need a streaming protocol. **Mitigation**: tackle CORR-018 first (the response side is the most constrained), then CORR-017, then CORR-019.

### Bundle 1 is the only HIGH-severity

SEC-007 is M-effort and security-sensitive. **Mitigation**: take it as a single-bundle, single-commit fix; extra review effort on the path-resolution allowlist logic.

### Stale-bookkeeping items (SEC-011, OBS-007, OBS-009) are inside B24

These are 1-line YAML updates but they could be done in B25 too. The decision to keep them in B24 is for visibility (the bundle name says "stale bookkeeping").

### M-effort designs may need iteration

The M-effort designs above are 1-paragraph sketches. Some will need follow-up once the implementation starts (e.g. B11's streaming protocol may need a 2nd story once the first one lands). **Mitigation**: when a design needs more than 1 commit, split it into multiple stories and create a follow-up review entry — but do not auto-expand the bundle.

### `just validate` is the final gate

The verify gate (`just validate`) runs all tests including integration. Pre-existing flakiness in `tests/integration/test_worker_integration.py` and `tests/integration/test_tls.py` is **out of scope** for Round 2; if it surfaces, the user decides whether to skip or fix.

## 7. Open decisions deferred to first-tackle

These will be re-evaluated when Bundle 1 lands:

- Whether to land all 8 stories of B19 as one mega-commit or split per-concern.
- Whether to land B10's 3 M-effort stories as one commit or one per design.
- Whether B11's 3 streaming stories should share a single Protocol design (extracted to a follow-up) or land independently.

## 8. Per-bundle commit cadence (preview)

| Bundle | Stories | Expected commits | Notes |
|---|---|---|---|
| B1 | 1 | 1 | M effort, single story |
| B2 | 6 | 3-4 | group by file (HttpWorker + tests) |
| B3 | 6 | 4-5 | mostly tests, low-risk |
| B4 | 6 | 4-5 | settings loaders + tests |
| B5 | 4 | 3 | YAML + 1-line wiring |
| B6 | 2 | 2 | small, single file each |
| B7 | 6 | 4-5 | exception type narrowing |
| B8 | 5 | 3-4 | observability, low-risk |
| B9 | 5 | 4-5 | security, careful per-file |
| B10 | 7 | 5-6 | M-effort anchors |
| B11 | 4 | 4 | M-effort, one per design |
| B12 | 8 | 5-6 | M-effort, one per design |
| B13 | 3 | 3 | small, plan-time check |
| B14 | 4 | 3-4 | worker cleanup |
| B15 | 3 | 3 | M-effort, one per design |
| B16 | 7 | 5-6 | small + 1 ARCH refactor |
| B17 | 6 | 4-5 | M-effort, protocol introductions |
| B18 | 3 | 2-3 | enum introduction |
| B19 | 8 | 5-6 | M-effort + mechanical |
| B20 | 5 | 3-4 | tests, low-risk |
| B21 | 6 | 4-5 | tests + 1 redis refactor |
| B22 | 7 | 5-6 | tests, fixture work |
| B23 | 6 | 4-5 | docs + 1 EXC M effort |
| B24 | 5 | 3-4 | auth + 3 stale-bookkeeping |
| B25 | 0 | 1-2 | summary.md only |
| **Total** | **123** | **~85-105** | |

## 9. Hand-off to `writing-plans` skill

After spec approval, `writing-plans` will turn each bundle's design + commit cadence into a per-bundle implementation plan (test list, file paths, verify commands, subagent prompts). The plan will be 25 sub-plans; each sub-plan lands independently per the cadence above.
