---
branch: chore/code-review-update
initial_review_commit: 23c29e1
last_updated_commit: 63faed4
last_staleness_scan:
  commit: 63faed4
  date: 2026-06-21
---

# Code quality

## MAINT — Maintainability

**Grade:** B

MAINT-003 and MAINT-004 remain verified. MAINT-002 and MAINT-005 are re-resolved at slightly shifted line numbers. Three new MAINT findings from Layer 11: MAINT-006 (the registration-token block in `Orchestrator.start()` is inlined; also logs the token in plaintext at INFO level — see SEC-008 for the security side), MAINT-007 (RunPod + HuggingFace providers duplicate the HTTP fetch envelope), MAINT-008 (`HealthMonitor._handle_failure` reassigns its `error` parameter).

### MAINT-001 — BatchAsyncExecutor is a verbatim duplicate of AsyncExecutor; entire batch submission machinery is vestigial

```yaml
status: verified
severity: high
effort: M
reviewed_at: 23c29e1
last_verified_at:
  commit: a1b11b2
  date: 2026-06-19
fixed_in: ["e0da69f"]
files:
  - path: src/acheron/shell/executors/batch_async.py
    lines: 16-79
  - path: src/acheron/shell/executors/async_executor.py
    lines: 16-75
  - path: src/acheron/cli.py
    lines: 141
  - path: src/acheron/core/interfaces.py
    lines: 35-51
  - path: src/acheron/shell/transports/grpc.py
    lines: 38-125
  - path: src/acheron/core/models.py
    lines: 114
  - path: src/acheron/core/planner.py
    lines: 134-198
related: [CORR-002, ARCH-001, CORR-003]
```

**Issue.** `BatchAsyncExecutor.run` (batch_async.py:26-79) is byte-identical to `AsyncExecutor.run` (async_executor.py:22-75) — a direct diff confirms only the docstring differs. The docstring promises "Batch-flagged steps receive all outputs from completed preceding steps so the handler can construct a BatchJob with the correct payloads," but the body just calls `self._handler(step, plan)` per step exactly like AsyncExecutor; no BatchJob is ever constructed. No executor ever calls `StreamingWorker.submit_batch`/`poll_batch`/`collect_results` (interfaces.py:39-51), so the GrpcWorker (grpc.py:103-125) and HttpWorker (http.py:77-92) batch methods are dead. `PlanStep.batch` (models.py:114) is set `True` on synthesize steps (planner.py:134,198) and serialized by Redis (redis.py:123) but read by nothing. Worse, `batch_async` is the CLI default (cli.py:141 `default="batch_async"`). Latent bug if ever wired up: `GrpcWorker._batches` (grpc.py:38) is per-instance state, but `step_handler.py:112` creates a fresh worker per step, so batch handles could not survive across calls.

**Why it matters.** AGENTS.md states "Silent/unexpected behavior is worse than no control at all." Users selecting batch_async for GPU throughput get plain async — correct outputs, but the advertised optimization is a no-op. The dead batch surface (StreamingWorker ABC, BatchJob/BatchStatus models, transport batch methods, PlanStep.batch field) is misleading API surface that must be maintained for nothing. High because it is misleading-by-default.

**Recommendation.** Per greenfield rules, either implement batch submission (collect upstream outputs for batch-flagged steps, build a BatchJob, call submit_batch/poll_batch/collect_results on a StreamingWorker reused across the batch) or remove BatchAsyncExecutor, the StreamingWorker batch methods, BatchJob/BatchStatus, PlanStep.batch, and change the CLI default to a strategy that exists. Do not leave the stub.

**Verification.** `just test` — add a test asserting batch_async submits a BatchJob to a StreamingWorker (currently impossible). `just type-check`. If removing: grep for `submit_batch|BatchJob|BatchStatus|\.batch` returns zero src hits.

### MAINT-002 — redis.py hand-rolls JSON ser/deser for domain models that cache.py serializes via pydantic, duplicating and drifting

```yaml
status: open
severity: medium
effort: M
reviewed_at: 23c29e1
last_verified_at:
  commit: pending
  date: 2026-06-21
fixed_in: []
files:
  - path: src/acheron/shell/stores/redis.py
    lines: 30-280
  - path: src/acheron/shell/cache.py
    lines: 28-112
related: [DATA-002]
```

**Issue.** redis.py hand-rolls ~150 lines of JSON serialization for TrackedJob/Plan/PlanStep/PlanResult/WorkerCapabilities (`_serialize_job`, `_deserialize_job`, `_serialize_capabilities`, `_deserialize_capabilities`, `_deserialize_worker` — redis.py:30-270) via json.dumps/loads with field-by-field reconstruction and a manual `source_type` match dispatch. cache.py serializes the same Plan/OutputFile models via pydantic `TypeAdapter` (cache.py:44,59,85,107 — `_plan_adapter.dump_json`/`validate_json`). Adding a field to PlanStep/Plan/PlanResult requires editing the redis ser site and the deser site separately, while cache.py adapts automatically. The manual deser re-derives `source_type` via a match on AudioRequest/EpubRequest, duplicating planner logic rather than persisting it. The fundamental duplication remains — redis.py is still the largest file in the shell package.

**Why it matters.** Two divergent serialization paths for the same domain models is a fragility hotspot: field additions silently desync between the Redis and disk-cache backends, and a round-trip mismatch would surface only at runtime. Medium — no current bug, but a clear maintainability and drift risk in the largest file.

**Recommendation.** Introduce a shared serialization module using pydantic TypeAdapter for TrackedJob (and the existing Plan/OutputFile adapters) and use it in both cache.py and redis.py. Eliminate the manual source_type match by persisting source_type or relying on the tagged union. This collapses ~150 lines and makes both backends share one schema.

**Verification.** `just test` (redis round-trip tests still pass), `just type-check`. Add a PlanStep field and confirm zero redis.py edits are needed.

### MAINT-003 — _BUILT_IN_LOCAL_HANDLERS private symbol imported across module boundary

```yaml
status: verified
severity: low
effort: S
reviewed_at: a1b11b2
last_verified_at:
  commit: be7b3ab
  date: 2026-06-20
fixed_in:
  - 92ed9da
files:
  - path: src/acheron/shell/orchestrator.py
    lines: 19-20
  - path: src/acheron/shell/local_handlers.py
    lines: 90
related: ['ARCH-005']
```

**Issue.** The refactor that extracted capability aggregation to capabilities.py also moved _BUILT_IN_LOCAL_HANDLERS from orchestrator.py to local_handlers.py while keeping the leading underscore. orchestrator.py:20 now `from acheron.shell.local_handlers import _BUILT_IN_LOCAL_HANDLERS, ...` — a private-by-convention name imported across the module boundary. Other module-private helpers in this codebase (e.g. _collect_worker_caps, _pair_is_achievable in capabilities.py) stay module-internal. The leading underscore signals 'do not import', but the orchestrator cannot start without it.

**Why it matters.** A leading-underscore name imported externally misleads readers and tools (e.g. IDE 'unused symbol' hints, future refactor scripts) into treating it as private. Future moves/renames of local_handlers internals will silently break the orchestrator. The pattern is also inconsistent with the rest of the refactor, which correctly made all_languages_caps public (no underscore).

**Recommendation.** Either rename _BUILT_IN_LOCAL_HANDLERS to BUILT_IN_LOCAL_HANDLERS (public) in local_handlers.py and update the orchestrator import, or expose a public factory function get_built_in_local_handlers() -> dict[WorkerType, LocalJobHandler] in local_handlers.py. The dataclass-frozen list of (WorkerType -> handler) is a stable public contract — the underscore is misleading.

**Verification.** grep -rn 'from acheron.shell.local_handlers import' src/ should not pull a name starting with underscore; `just lint-strict`, `just test`

### MAINT-004 — _stage return value in StreamingExecutor is dead code — computed cost returned as float|None but never consumed by _run_pipeline

```yaml
status: verified
severity: low
effort: S
reviewed_at: d0b739b
last_verified_at:
  commit: be7b3ab
  date: 2026-06-20
fixed_in:
  - 640bb03
files:
  - path: src/acheron/shell/executors/streaming.py
    lines: 190-245
  - path: src/acheron/shell/executors/streaming.py
    lines: 78-109
related: [CORR-008]
```

**Issue.** `_stage` (streaming.py:180-237) returns `float | None` (cost or None) and its docstring (line 189) says "Returns the step's cost (including on FAILED), None if no work ran." However, `_run_pipeline` (streaming.py:67-98) calls `task.result()` on each stage task without capturing the return value (line 95: `task.result()` — no assignment). Costs are instead read from the shared `stage_costs` list parameter. The return value of `_stage` is computed at line 237 (`return stage_costs[stage_index]`) but silently dropped.

**Why it matters.** A misleading type signature and docstring wastes reader effort tracing data flow that doesn't exist. The dead return value survived the CORR-008 fix because the migration from per-task return to shared list was incomplete — it captured cost into the list but forgot to remove the return path.

**Recommendation.** Change `_stage` return type to `None`, remove the `return stage_costs[stage_index]` line, and update the docstring to say "Records cost in stage_costs[stage_index]." Three-line fix.

**Verification.** `just test`; `just type-check`; grep `def _stage` confirms no return type annotation.

### MAINT-005 — Orchestrator._execute duplicates PlanResult construction across adjacent exception handlers

```yaml
status: open
severity: low
effort: S
reviewed_at: be7b3ab
last_verified_at:
  commit: 63faed4
  date: 2026-06-21
fixed_in: []
files:
  - path: src/acheron/shell/orchestrator.py
    lines: 319-344
related: [OBS-004]
```

**Issue.** `Orchestrator._execute` duplicates the same 9-field `PlanResult` constructor in its `except AcheronError` and `except Exception` blocks. The two blocks differ only in their log messages; the failure result is identical.

**Why it matters.** Duplicated domain-object construction across adjacent exception handlers is a small maintainability hotspot. Adding a field to `PlanResult` or changing failure semantics requires editing both sites, and they can drift silently.

**Recommendation.** Extract a helper such as `_record_failure(tracked, exc)` that sets `tracked.status = PlanStatus.FAILED` and builds the `PlanResult` once, called from both exception handlers.

**Verification.** `just test`; `just type-check`. Both except blocks should reduce to a single helper call.

### MAINT-006 — Orchestrator.start() inlines 17-line registration-token block; logs the token in plaintext

```yaml
status: open
severity: medium
effort: S
reviewed_at: 63faed4
last_verified_at:
  commit: 63faed4
  date: 2026-06-21
fixed_in: []
files:
  - path: src/acheron/shell/orchestrator.py
    lines: 177-194
related: [SEC-008, MAINT-007]
```

**Issue.** `Orchestrator.start()` (orchestrator.py:177-194) embeds a 17-line block that loads or generates the registration token, reads/writes a side file at `<data_dir>/.registration_token`, and logs the result. The block interleaves two concerns — orchestrator lifecycle wiring and credentials management — and `start()` is now 34 lines where a 5-line declarative method that calls a helper would be clearer. The block is also the largest single responsibility creep inside `start()` since the `HealthProviders` wiring at lines 77-82. Separately, the log message at orchestrator.py:192 emits the freshly generated token at INFO level — see SEC-008 for the security side.

**Why it matters.** Inline startup concerns make `start()` harder to test (does too much to factor out) and harder to read; the token block is unrelated to the other startup steps (verify_data_dir_writable, connect stores, register local workers, start health monitor). Mixed responsibilities in a single method is a maintainability hotspot.

**Recommendation.** Extract the token block to a private coroutine `_load_or_create_registration_token() -> None` that mutates `self._settings.orchestrator.registration_token`. Replace the inline block with a single call. In the helper, log only that a token was generated or loaded — do NOT log the token value itself.

**Verification.** `just test`, `just type-check`. `Orchestrator.start()` should be 5-7 lines of declarative wiring; a test asserting an existing token file is not overwritten and a missing file yields a fresh token; INFO logs do not contain the token.

### MAINT-007 — RunPodHealthProvider and HuggingFaceHealthProvider duplicate the HTTP fetch envelope

```yaml
status: open
severity: medium
effort: S
reviewed_at: 63faed4
last_verified_at:
  commit: 63faed4
  date: 2026-06-21
fixed_in: []
files:
  - path: src/acheron/shell/health_providers.py
    lines: 39-53
  - path: src/acheron/shell/health_providers.py
    lines: 70-94
related: [MAINT-006]
```

**Issue.** `RunPodHealthProvider.check_status` (health_providers.py:39-53) and `HuggingFaceHealthProvider.check_status` (health_providers.py:70-94) share an identical 8-line fetch envelope: `try` → `async with httpx.AsyncClient() as client: resp = await client.get(url, headers=headers, timeout=10.0)` → `except (httpx.HTTPError, OSError)` → return `WorkerStatus.OFFLINE` → status code check. The two methods only diverge in URL construction and how they map the response body to BOOTING/OFFLINE. Adding a third provider (SageMaker, Vast.ai) requires re-typing the same fetch envelope.

**Why it matters.** Bundle-level MAINT: 2 sites of ~10 lines of duplicated platform-agnostic HTTP plumbing. The `HealthProvider` ABC is the right abstraction for the diverging response mapping; the fetch envelope is a separate, common concern.

**Recommendation.** Extract a private helper `async def _fetch_provider_response(path: str, headers: dict[str, str]) -> httpx.Response | None` that handles the `AsyncClient` lifecycle, timeout, and error handling; each provider delegates. The providers retain only URL construction and body interpretation.

**Verification.** `just test`, `just type-check`. Adding a third provider should require zero new fetch boilerplate.

### MAINT-008 — HealthMonitor._handle_failure reassigns its `error` parameter inside the try/except

```yaml
status: open
severity: low
effort: S
reviewed_at: 63faed4
last_verified_at:
  commit: 63faed4
  date: 2026-06-21
fixed_in: []
files:
  - path: src/acheron/shell/health.py
    lines: 133-152
related: [EXC-003]
```

**Issue.** `_handle_failure(self, worker: RegisteredWorker, error: str)` accepts the probe error message as a parameter, but inside the provider try/except it reassigns `error = f"{error}; provider {provider_name} error: {exc}"` (health.py:144). The parameter is therefore both an input and a locally-mutated accumulator; the chained error message is only valid after the reassignment, and a reader tracing data flow has to check both the parameter declaration and the assignment to know what `error` is at the call sites (146, 149).

**Why it matters.** Reassigning a function parameter to a new value is a naming/clarity smell per the lens. The construct is also the kind of thing basedpyright/pylint PAL would flag under stricter checks.

**Recommendation.** Introduce a local `chained_error = error` and build the chained message after the except block; pass `chained_error` to `set_worker_status`. The mutation disappears and the parameter is read-only.

**Verification.** `just test`, `just type-check`; add a test that exercises a provider raising — the worker should be marked OFFLINE with the chained error string and the parameter should remain unchanged at function return.

## EXC — Exception discipline

**Grade:** B

EXC-001 (medium) remains open: `tenacity` is still unused and transient network calls have no retry. EXC-002 is verified. One new low finding: EXC-003 — `HealthMonitor._handle_failure` wraps the platform provider call in a bare `except Exception` with `# noqa: BLE001`; the recovery path belongs inside the provider contract.

### EXC-001 — tenacity dependency is unused; WorkerTimeoutError/PlanValidationError are never raised; transient network calls have no retry

```yaml
status: open
severity: medium
effort: M
reviewed_at: 23c29e1
last_verified_at:
  commit: pending
  date: 2026-06-21
fixed_in: []
files:
  - path: pyproject.toml
    lines: 22
  - path: src/acheron/core/errors.py
    lines: 16-29
  - path: src/acheron/shell/executors/streaming.py
    lines: 233-235
  - path: src/acheron/shell/transports/grpc.py
    lines: 64-72
  - path: src/acheron/shell/transports/http.py
    lines: 39-52
related: []
```

**Issue.** `tenacity~=9.1` is a production dependency (pyproject.toml:20) with zero imports across src/ and tests/. No network call is retried: GrpcWorker.execute (a streaming RPC, grpc.py:67-75) and HttpWorker._request (http.py:44-59) raise on the first transient failure; Redis store ops have no retry. `WorkerTimeoutError` (errors.py:28) is defined and exported but never raised — streaming.py:213 catches `TimeoutError` and raises plain `WorkerError`, discarding the more specific type. `PlanValidationError` (errors.py:16) is likewise defined/exported but never raised.

**Why it matters.** A dead dependency and dead exception types are misleading surface area. Remote worker and Redis calls are exactly where transient failures occur; the absence of retries means a single transient network blip fails a whole step/job. The timeout-specific exception exists but is not used, so callers cannot distinguish a timeout from other worker errors. Medium — no current data loss, but fragile in normal operation and contradicts the declared dependency.

**Recommendation.** Pick one direction per greenfield rules: either remove tenacity, WorkerTimeoutError, and PlanValidationError (YAGNI), or wire tenacity retries into transport/Redis call sites on transient errors only (gRPC UNAVAILABLE/cancelled, HTTP ConnectError/429/5xx, Redis ConnectionError) and raise WorkerTimeoutError on timeout. Do not leave the stubs.

**Verification.** `just validate`. If removing: grep for `tenacity|WorkerTimeoutError|PlanValidationError` returns zero src hits. If adding: tests asserting retry-then-succeed on a flaky stub and timeout→WorkerTimeoutError.

### EXC-002 — Broad `except Exception` at boundary sites is mostly well-applied but two could name narrower types

```yaml
status: verified
severity: low
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: be7b3ab
  date: 2026-06-20
fixed_in:
  - a5b1ff0
files:
  - path: src/acheron/core/chunking.py
    lines: 28
  - path: src/acheron/shell/cache.py
    lines: 60
  - path: src/acheron/shell/cache.py
    lines: 108
  - path: src/acheron/shell/orchestrator.py
    lines: 123-128
  - path: src/acheron/shell/orchestrator.py
    lines: 216-238
  - path: src/acheron/shell/executors/streaming.py
    lines: 230
  - path: src/acheron/shell/executors/streaming.py
    lines: 244
related: []
```

**Issue.** There are 7 `except Exception` sites in src. Most are defensible task/stage boundaries that wrap into domain errors and log: orchestrator.close (orchestrator.py:116, exception-isolated teardown per docstring), orchestrator._execute (orchestrator.py:216, top-level task boundary → status='failed'), streaming._stage (streaming.py:218,232 → PipelineError). Two could be narrower: chunking.py:28 wraps `nltk.sent_tokenize` in `ChunkingError` but NLTK's common failure is `LookupError` (missing punkt data) — naming it would distinguish missing-data from genuine parse failures; cache.py:60,108 wrap pydantic validation in `CacheCorruptedError` where `pydantic.ValidationError` is the expected type and a broad catch could mask an unrelated OSError or AttributeError bug in the adapter call.

**Why it matters.** Narrower exceptions at these two sites would prevent masking programming errors (e.g., AttributeError from a refactoring bug) as "corruption." Low — the current behavior is correct for the expected cases and all sites log/wrap rather than silently swallow.

**Recommendation.** Narrow chunking.py:28 to `(LookupError, OSError, ...)` as appropriate; narrow cache.py:60,108 to `pydantic.ValidationError` (chaining preserves the original). Leave the orchestrator/streaming boundary catches as-is.

**Verification.** `just test`, `just type-check`. Confirm a missing-punkt scenario still raises ChunkingError and a malformed manifest still raises CacheCorruptedError.

### EXC-003 — HealthMonitor._handle_failure catches bare `Exception` from the platform provider; recovery should live inside the provider contract

```yaml
status: open
severity: low
effort: S
reviewed_at: 63faed4
last_verified_at:
  commit: 63faed4
  date: 2026-06-21
fixed_in: []
files:
  - path: src/acheron/shell/health.py
    lines: 139-145
  - path: src/acheron/shell/health_providers.py
    lines: 39-53
  - path: src/acheron/shell/health_providers.py
    lines: 70-94
related: [MAINT-008, OBS-005]
```

**Issue.** The platform-provider call inside `_handle_failure` (health.py:139-145) wraps `await provider.check_status(endpoint_id)` in `except Exception as exc:  # noqa: BLE001`. The recovery logs a warning, forces `platform_status = WorkerStatus.OFFLINE`, and chains the error. The broad catch masks programming errors (`TypeError`, `AttributeError`) inside a provider as 'provider transient error'. The `HealthProvider` ABC docstring already promises 'returns OFFLINE on platform error' but the contract is not enforced by the type or by an internal try/except in either implementation.

**Why it matters.** Pattern-level EXC: a third provider that adds a different exception class (e.g. an SDK raising `boto3.ClientError`) would be caught here, hiding transient and permanent failures behind the same log line. A fourth provider that raises `AttributeError` on a payload shape change would be reported as 'provider transient error' rather than a programming bug. The existing `_check_http_health` and `_check_grpc_health` in `health.py:44-65` already swallow narrower `(httpx.HTTPError, OSError)` correctly — apply the same pattern to the providers.

**Recommendation.** Move the swallow into each provider's `check_status`: `RunPodHealthProvider` and `HuggingFaceHealthProvider` catch `(httpx.HTTPError, OSError, ValueError, KeyError)` and return `WorkerStatus.OFFLINE`. `HealthMonitor._handle_failure` then trusts the contract and removes its try/except.

**Verification.** `just test`, `just type-check`. Drop the noqa and the try/except in `_handle_failure`; providers gain the swallow and the `HealthProvider` ABC contract is enforced by the implementation.

## TYPE — Type safety

**Grade:** B

TYPE-002 remains verified (PlanStatus enum at ad78be4). TYPE-001 (medium) persists for the `AcheronClient` `dict[str, Any]` return type consumed via magic-string keys in the CLI; the metadata contract sub-issue is resolved. Two new low/medium TYPE findings: TYPE-003 (8 `# type: ignore[misc]` markers in `redis.py` without per-site justification), TYPE-004 (`WorkerResponse.status` is stringly-typed despite a `WorkerStatus` enum existing at `core/models.py`).

### TYPE-001 — AcheronClient returns dict[str, Any] consumed via magic-string keys; metadata contracts partially resolved

```yaml
status: open
severity: medium
effort: M
reviewed_at: 23c29e1
last_verified_at:
  commit: 63faed4
  date: 2026-06-21
fixed_in: []
files:
  - path: src/acheron/api_client.py
    lines: 41-128
  - path: src/acheron/cli.py
    lines: 169-255
related: [ARCH-004]
```

**Issue.** Every `AcheronClient` method returns `dict[str, Any]` or `list[dict[str, Any]]` (api_client.py:35,51,58,65,76). The CLI then indexes these with magic strings — `result['job_id']`, `j['status']`, `j['worker_id']`, `p['workers']`, `result['errors']` (cli.py:169-236); any key typo is a runtime KeyError. Typed pydantic response schemas (JobResponse, WorkerResponse, LanguagePair) already exist in schemas.py but are not used on the client side. The metadata contract sub-issue is resolved: `RegisteredWorker.metadata` (registry.py:27), `WorkerStore.register` metadata (stores/base.py:30), and schemas.py:55 are now all `dict[str, JsonValue]`, matching `WorkerCapabilities.metadata`.

**Why it matters.** AGENTS.md explicitly says avoid `Any` and avoid "Mapping[str, Any] as documentation-via-runtime-error contract." The CLI's untyped consumption of the API is exactly that pattern. Medium — works today but defeats the type checker on a public boundary.

**Recommendation.** Have `AcheronClient` return the existing pydantic response models (JobResponse, WorkerResponse, CapabilitiesResponse) instead of raw dicts; have the CLI consume typed models via attribute access.

**Verification.** `just type-check`, `just lint-strict`. CLI key access becomes attribute access verified by mypy; a typo is now a compile error.

### TYPE-002 — PlanResult.status and TrackedJob.status are stringly-typed with a vocabulary diverging from the JobStatus enum

```yaml
status: verified
severity: medium
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: d0b739b
  date: 2026-06-20
fixed_in:
  - ad78be49740dd3d5094ba30a376593c1e2e499cb
files:
  - path: src/acheron/core/models.py
    lines: 133
  - path: src/acheron/shell/job_store.py
    lines: 21
  - path: src/acheron/shell/orchestrator.py
    lines: 180-218
  - path: src/acheron/shell/executors/sequential.py
    lines: 56
  - path: src/acheron/shell/executors/async_executor.py
    lines: 64
  - path: src/acheron/shell/executors/streaming.py
    lines: 158-237
related: [CORR-001]
```

**Issue.** `JobResult.status` was typed `JobStatus` (enum: success/failed/partial) but `PlanResult.status` (models.py:135) and `TrackedJob.status` (job_store.py:21) were `str`, assigned via bare string literals across 10+ sites. A typo like 'complted' compiled and silently produced a wrong status; orchestrator._execute set `tracked.status='failed'` (string) while the underlying JobResult carried `JobStatus.FAILED` (enum) — two representations of the same concept with no compiler enforcement.

**Why it matters.** Violated AGENTS.md "make illegal states unrepresentable" and "avoid string-based dispatch." Status typos were unchecked and the dual success/completed vocabulary was a latent confusion bug. Medium — not currently broken but a clear typing gap.

**Recommendation.** Introduce a `PlanStatus` enum for `PlanResult.status` and `TrackedJob.status`; replace all string-literal assignments with enum members. Align the 'completed'/'success' vocabularies.

**Verification.** `just type-check` (mypy flags string assignments to enum fields), `just lint-strict`. grep for `status\s*=\s*["']` returns zero hits in src.

### TYPE-003 — redis.py accumulates 8 `# type: ignore[misc]` markers on `await self._redis.<method>()` calls

```yaml
status: open
severity: medium
effort: M
reviewed_at: 63faed4
last_verified_at:
  commit: 63faed4
  date: 2026-06-21
fixed_in: []
files:
  - path: src/acheron/shell/stores/redis.py
    lines: 293
  - path: src/acheron/shell/stores/redis.py
    lines: 324
  - path: src/acheron/shell/stores/redis.py
    lines: 331
  - path: src/acheron/shell/stores/redis.py
    lines: 358
  - path: src/acheron/shell/stores/redis.py
    lines: 359
  - path: src/acheron/shell/stores/redis.py
    lines: 385
  - path: src/acheron/shell/stores/redis.py
    lines: 402
  - path: src/acheron/shell/stores/redis.py
    lines: 424
related: []
```

**Issue.** `redis.py:3-5` documents that `redis.asyncio` stubs type methods as `Awaitable[T] | T` and that the `T` branch is unreachable in async call sites. Despite the file-level justification, every `await self._redis.<method>()` call site carries a `# type: ignore[misc]` marker (8 sites: `ping` x2 at 293/402, `hgetall` at 324, `smembers` x2 at 331/424, `hincrby` at 358, `hset` at 359, `hset-with-mapping` at 385). The markers do not have per-site justification comments, and the rationale depends on a single header paragraph that could be deleted in a future refactor pass. The new `set_worker_status` method (lines 375-388) added another `hset(mapping=...)` call site at line 385, growing the list. A future `redis-py` version that fixes the stubs would leave the markers as dead annotations.

**Why it matters.** Pattern-level TYPE: each marker is a future-typing-debt accumulator per the rubric. AGENTS.md says "Avoid linter and type ignores in general without a very good reason that should be explicitly explained to the user" — the explanation exists at the file level but the per-site obligation is not met.

**Recommendation.** Either (a) add a one-line `# noqa: misc: redis.asyncio stubs Awaitable[T]|T; see file header` per site to make each marker self-documenting, or (b) centralize the redis access in a thin `RedisAwaitable` wrapper class with a single `# type: ignore[misc]` and a docstring. Option (b) collapses 8 markers to 1 and the next provider change touches one place.

**Verification.** `just type-check`, `just test`. The count of `# type: ignore[misc]` markers should drop to 0 (option b) or to 8 with consistent per-site comments (option a).

### TYPE-004 — WorkerResponse.status is stringly-typed despite a WorkerStatus enum existing at core/models.py

```yaml
status: open
severity: low
effort: S
reviewed_at: 63faed4
last_verified_at:
  commit: 63faed4
  date: 2026-06-21
fixed_in: []
files:
  - path: src/acheron/shell/api/schemas.py
    lines: 67-76
  - path: src/acheron/shell/api/routes/workers.py
    lines: 51
  - path: src/acheron/shell/api/routes/workers.py
    lines: 68
related: []
```

**Issue.** The new `WorkerStatus` enum is defined at `core/models.py:58-63` and used everywhere in `shell/` (registry, health, stores, health_providers). The API response schema `WorkerResponse.status` is typed `str = "healthy"` (schemas.py:75), and `routes/workers.py:51,68` call sites do `WorkerStatus.HEALTHY.value` to populate the schema. AGENTS.md says "avoid string-based dispatch" and "make illegal states unrepresentable" — the response schema is the exact anti-pattern. A typo in a test fixture like `status='healty'` would compile and pass pydantic validation; the `WorkerStatus` enum would reject it.

**Why it matters.** The internal API is fully enum-typed; the public API is the only stringly-typed surface. This is exactly the boundary the project wants typed. Low — no current bug, but a lost typo-check at the API boundary.

**Recommendation.** Change `WorkerResponse.status: WorkerStatus = WorkerStatus.HEALTHY` and let pydantic serialize the enum to a JSON string automatically. Update `routes/workers.py:51,68` to use `WorkerStatus.HEALTHY` and `w.status` directly (no `.value`).

**Verification.** `just test`, `just type-check`. Confirm a malformed `status='healty'` is now rejected by pydantic, and the response JSON still serializes to `"status": "healthy"`.
