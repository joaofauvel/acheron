---
branch: chore/code-review-update
initial_review_commit: 23c29e1
last_updated_commit: a1b11b2
last_staleness_scan:
  commit: a1b11b2
  date: 2026-06-19
---

# Code quality

## MAINT — Maintainability

**Grade:** A

Two open findings: redis.py hand-rolls JSON ser/deser for domain models that cache.py serializes via pydantic (medium, drift risk); and the module-private `_BUILT_IN_LOCAL_HANDLERS` symbol is imported across the orchestrator boundary (low). MAINT-001 is verified.

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
  commit: a1b11b2
  date: 2026-06-19
fixed_in: []
files:
  - path: src/acheron/shell/stores/redis.py
    lines: 30-256
  - path: src/acheron/shell/cache.py
    lines: 80-127
  - path: src/acheron/shell/cache.py
    lines: 80-127
related: [DATA-002, DATA-003]
```

**Issue.** redis.py hand-rolls ~155 lines of JSON serialization for TrackedJob/Plan/PlanStep/PlanResult/WorkerCapabilities (`_serialize_job`, `_deserialize_job`, `_serialize_capabilities`, `_deserialize_capabilities`, `_deserialize_worker` — redis.py:30-258) via json.dumps/loads with field-by-field reconstruction and a manual `source_type` match dispatch (redis.py:134-141). cache.py serializes the same Plan/OutputFile models via pydantic `TypeAdapter` (cache.py:15-16, `_plan_adapter.dump_json`/`validate_json`). Adding a field to PlanStep/Plan/PlanResult requires editing the redis ser site and the deser site separately, while cache.py adapts automatically. The manual deser re-derives `source_type` via a match on AudioRequest/EpubRequest, duplicating planner logic rather than persisting it. This bulk is why redis.py is the largest file at 393 lines.

**Why it matters.** Two divergent serialization paths for the same domain models is a fragility hotspot: field additions silently desync between the Redis and disk-cache backends, and a round-trip mismatch would surface only at runtime. Medium — no current bug, but a clear maintainability and drift risk in the largest file.

**Recommendation.** Introduce a shared serialization module using pydantic TypeAdapter for TrackedJob (and the existing Plan/OutputFile adapters) and use it in both cache.py and redis.py. Eliminate the manual source_type match by persisting source_type or relying on the tagged union. This collapses ~155 lines and makes both backends share one schema.

**Verification.** `just test` (redis round-trip tests still pass), `just type-check`. Add a PlanStep field and confirm zero redis.py edits are needed.

## EXC — Exception discipline

**Grade:** A

One medium finding: `tenacity` is a declared but unused production dependency, and `WorkerTimeoutError`/`PlanValidationError` are defined but never raised — while transient network calls (gRPC, HTTP, Redis) have no retry. One low finding: two `except Exception` sites could name narrower types, though all boundary catches wrap and log rather than silently swallow.

### EXC-001 — tenacity dependency is unused; WorkerTimeoutError/PlanValidationError are never raised; transient network calls have no retry

```yaml
status: open
severity: medium
effort: M
reviewed_at: 23c29e1
last_verified_at:
  commit: a1b11b2
  date: 2026-06-19
fixed_in: []
files:
  - path: pyproject.toml
    lines: 21
  - path: src/acheron/core/errors.py
    lines: 16-29
  - path: src/acheron/shell/executors/streaming.py
    lines: 202-204
  - path: src/acheron/shell/transports/grpc.py
    lines: 66-71
  - path: src/acheron/shell/transports/http.py
    lines: 39-52
  - path: src/acheron/shell/stores/redis.py
    lines: 1-391
related: []
```

**Issue.** `tenacity~=9.1` is a production dependency (pyproject.toml:21) with zero imports across src/ and tests/ (grep confirms). No network call is retried: GrpcWorker.execute (a streaming RPC, grpc.py:67-75) and HttpWorker._request (http.py:44-59) raise on the first transient failure; Redis store ops (redis.py:270-292) have no retry. `WorkerTimeoutError` (errors.py:28) is defined and exported but never raised in src — streaming.py:202-204 catches `TimeoutError` and raises plain `WorkerError`, discarding the more specific type. `PlanValidationError` (errors.py:16) is likewise defined/exported but never raised.

**Why it matters.** A dead dependency and dead exception types are misleading surface area. Remote worker and Redis calls are exactly where transient failures occur; the absence of retries (with tenacity already available) means a single transient network blip fails a whole step/job. The timeout-specific exception exists but is not used, so callers cannot distinguish a timeout from other worker errors. Medium — no current data loss, but fragile in normal operation and contradicts the declared dependency.

**Recommendation.** Pick one direction per greenfield rules: either remove tenacity, WorkerTimeoutError, and PlanValidationError (YAGNI), or wire tenacity retries into transport/Redis call sites on transient errors only (gRPC UNAVAILABLE/cancelled, HTTP ConnectError/429/5xx, Redis ConnectionError) and raise WorkerTimeoutError on timeout. Do not leave the stubs.

**Verification.** `just validate`. If removing: grep for `tenacity|WorkerTimeoutError|PlanValidationError` returns zero src hits. If adding: tests asserting retry-then-succeed on a flaky stub and timeout→WorkerTimeoutError.

### EXC-002 — Broad `except Exception` at boundary sites is mostly well-applied but two could name narrower types

```yaml
status: open
severity: low
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: a1b11b2
  date: 2026-06-19
fixed_in: []
files:
  - path: src/acheron/core/chunking.py
    lines: 26-30
  - path: src/acheron/shell/cache.py
    lines: 106-110
  - path: src/acheron/shell/cache.py
    lines: 106-110
  - path: src/acheron/shell/orchestrator.py
    lines: 113-117, 213-218
  - path: src/acheron/shell/orchestrator.py
    lines: 113-117, 213-218
  - path: src/acheron/shell/executors/streaming.py
    lines: 202-209, 217-219
related: []
```

**Issue.** There are 7 `except Exception` sites in src. Most are defensible task/stage boundaries that wrap into domain errors and log: orchestrator.close (orchestrator.py:179, exception-isolated teardown per docstring), orchestrator._execute (orchestrator.py:279, top-level task boundary → status='failed'), streaming._stage (streaming.py:207,213 → PipelineError). Two could be narrower: chunking.py:28 wraps `nltk.sent_tokenize` in `ChunkingError` but NLTK's common failure is `LookupError` (missing punkt data) — naming it would distinguish missing-data from genuine parse failures; cache.py:60,108 wrap pydantic validation in `CacheCorruptedError` where `pydantic.ValidationError` is the expected type and a broad catch could mask an unrelated OSError or AttributeError bug in the adapter call.

**Why it matters.** Narrower exceptions at these two sites would prevent masking programming errors (e.g., AttributeError from a refactoring bug) as "corruption." Low — the current behavior is correct for the expected cases and all sites log/wrap rather than silently swallow.

**Recommendation.** Narrow chunking.py:28 to `(LookupError, OSError, ...)` as appropriate; narrow cache.py:60,108 to `pydantic.ValidationError` (chaining preserves the original). Leave the orchestrator/streaming boundary catches as-is.

**Verification.** `just test`, `just type-check`. Confirm a missing-punkt scenario still raises ChunkingError and a malformed manifest still raises CacheCorruptedError.

## TYPE — Type safety

**Grade:** A

Two medium findings: `PlanResult.status` and `TrackedJob.status` are stringly-typed with a vocabulary diverging from the `JobStatus` enum (success vs completed), assigned via bare string literals across 10+ sites; and `AcheronClient` returns `dict[str, Any]` consumed via magic-string keys while metadata contracts use `dict[str, object]` where `dict[str, JsonValue]` is required — both contradict AGENTS.md anti-Any guidance. The `# type: ignore` audit found all ignores justified (redis.asyncio stubs documented in-module; generated-proto attr-defined/no-untyped-call/no-any-return).

### TYPE-001 — AcheronClient returns dict[str, Any] consumed via magic-string keys; metadata contracts use dict[str, object] where dict[str, JsonValue] is required

```yaml
status: open
severity: medium
effort: M
reviewed_at: 23c29e1
last_verified_at:
  commit: a1b11b2
  date: 2026-06-19
fixed_in: []
files:
  - path: src/acheron/api_client.py
    lines: 41-110
  - path: src/acheron/cli.py
    lines: 169-254
  - path: src/acheron/shell/registry.py
    lines: 27
  - path: src/acheron/shell/stores/base.py
    lines: 30
  - path: src/acheron/shell/api/schemas.py
    lines: 49
  - path: src/acheron/core/models.py
    lines: 57
related: [ARCH-004]
```

**Issue.** Every `AcheronClient` method returns `dict[str, Any]` or `list[dict[str, Any]]` (api_client.py:35,51,58,65,76). The CLI then indexes these with magic strings — `result['job_id']`, `j['status']`, `j['worker_id']`, `p['workers']`, `result['errors']` (cli.py:169-236); any key typo is a runtime KeyError. Typed pydantic response schemas (JobResponse, WorkerResponse, LanguagePair) already exist in schemas.py but are not used on the client side. Separately, `RegisteredWorker.metadata` (registry.py:27) and `WorkerStore.register`'s metadata param (stores/base.py:30) are typed `dict[str, object]`, which claims to accept any object, but Redis serializes metadata via `json.dumps` (redis.py:85) — non-JSON values fail at runtime. `WorkerCapabilities.metadata` is correctly `dict[str, JsonValue]` (models.py:58); the store/registry contracts do not match. schemas.py:49 mirrors the looseness with `dict[str, Any]`.

**Why it matters.** AGENTS.md explicitly says avoid `Any` and avoid "Mapping[str, Any] as documentation-via-runtime-error contract." The CLI's untyped consumption of the API is exactly that pattern, and the `dict[str, object]` metadata contract is a runtime-error-in-waiting. Medium — works today but defeats the type checker on a public boundary.

**Recommendation.** Have `AcheronClient` return the existing pydantic response models (JobResponse, WorkerResponse, CapabilitiesResponse) instead of raw dicts; have the CLI consume typed models via attribute access. Change metadata contracts in registry.py, stores/base.py, and schemas.py to `dict[str, JsonValue]` to match WorkerCapabilities and enforce serializability at the type level.

**Verification.** `just type-check`, `just lint-strict`. CLI key access becomes attribute access verified by mypy; a typo is now a compile error.

### TYPE-002 — PlanResult.status and TrackedJob.status are stringly-typed with a vocabulary diverging from the JobStatus enum

```yaml
status: fixed
severity: medium
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: pending
  date: 2026-06-19
fixed_in:
  - pending
files:
  - path: src/acheron/core/models.py
    lines: 133
  - path: src/acheron/core/models.py
    lines: 133
  - path: src/acheron/shell/job_store.py
    lines: 21
  - path: src/acheron/shell/orchestrator.py
    lines: 180, 195, 205, 215, 218
  - path: src/acheron/shell/executors/sequential.py
    lines: 56
  - path: src/acheron/shell/executors/async_executor.py
    lines: 64
  - path: src/acheron/shell/executors/streaming.py
    lines: 158, 168, 211-213, 225, 229
related: [CORR-001]
```

**Issue.** `JobResult.status` is typed `JobStatus` (enum: success/failed/partial) but `PlanResult.status` (models.py:135) and `TrackedJob.status` (job_store.py:21) are `str`, assigned via bare string literals across 10+ sites: 'running'/'completed'/'failed'/'partial' (orchestrator.py:243,258,278,281; executors at sequential.py:49, async_executor.py:64, batch_async.py:68, streaming.py:158,168,225). The string vocabulary ('completed') diverges from the enum vocabulary ('success'). A typo like 'complted' compiles and silently produces a wrong status; orchestrator._execute sets `tracked.status='failed'` (string) while the underlying JobResult carries `JobStatus.FAILED` (enum) — two representations of the same concept with no compiler enforcement.

**Why it matters.** Violates AGENTS.md "make illegal states unrepresentable" and "avoid string-based dispatch." Status typos are unchecked and the dual success/completed vocabulary is a latent confusion bug for any consumer comparing JobResult to PlanResult. Medium — not currently broken but a clear typing gap the project's own guidelines call out.

**Recommendation.** Introduce a `PlanStatus`/`JobLifecycleStatus` enum (or reuse JobStatus with aligned values) for `PlanResult.status` and `TrackedJob.status`; replace all string-literal assignments with enum members. Align the 'completed'/'success' vocabularies or document why they differ.

**Verification.** `just type-check` (mypy flags string assignments to enum fields), `just lint-strict`. grep for `status\s*=\s*["']` returns zero hits in src.

### MAINT-003 — _BUILT_IN_LOCAL_HANDLERS private symbol imported across module boundary

```yaml
status: open
severity: low
effort: S
reviewed_at: a1b11b2
last_verified_at:
  commit: a1b11b2
  date: 2026-06-19
fixed_in: []
files:
  - path: src/acheron/shell/orchestrator.py
    lines: 20
  - path: src/acheron/shell/local_handlers.py
    lines: 89-93
related: ['ARCH-005']
```

**Issue.** The refactor that extracted capability aggregation to capabilities.py also moved _BUILT_IN_LOCAL_HANDLERS from orchestrator.py to local_handlers.py while keeping the leading underscore. orchestrator.py:20 now `from acheron.shell.local_handlers import _BUILT_IN_LOCAL_HANDLERS, ...` — a private-by-convention name imported across the module boundary. Other module-private helpers in this codebase (e.g. _collect_worker_caps, _pair_is_achievable in capabilities.py) stay module-internal. The leading underscore signals 'do not import', but the orchestrator cannot start without it.

**Why it matters.** A leading-underscore name imported externally misleads readers and tools (e.g. IDE 'unused symbol' hints, future refactor scripts) into treating it as private. Future moves/renames of local_handlers internals will silently break the orchestrator. The pattern is also inconsistent with the rest of the refactor, which correctly made all_languages_caps public (no underscore).

**Recommendation.** Either rename _BUILT_IN_LOCAL_HANDLERS to BUILT_IN_LOCAL_HANDLERS (public) in local_handlers.py and update the orchestrator import, or expose a public factory function get_built_in_local_handlers() -> dict[WorkerType, LocalJobHandler] in local_handlers.py. The dataclass-frozen list of (WorkerType -> handler) is a stable public contract — the underscore is misleading.

**Verification.** grep -rn 'from acheron.shell.local_handlers import' src/ should not pull a name starting with underscore; `just lint-strict`, `just test`
