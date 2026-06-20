---
branch: chore/code-review-update
initial_review_commit: 23c29e1
last_updated_commit: d0b739b
last_staleness_scan:
  commit: d0b739b
  date: 2026-06-20
---

# Code quality

## MAINT — Maintainability

**Grade:** A

MAINT-003 and MAINT-004 are now verified (_BUILT_IN_LOCAL_HANDLERS renamed public; dead _stage return value removed). MAINT-002 remains open.

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
  commit: d0b739b
  date: 2026-06-20
fixed_in: []
files:
  - path: src/acheron/shell/stores/redis.py
    lines: 30-270
  - path: src/acheron/shell/cache.py
    lines: 44-107
related: [DATA-002]
```

**Issue.** redis.py hand-rolls ~150 lines of JSON serialization for TrackedJob/Plan/PlanStep/PlanResult/WorkerCapabilities (`_serialize_job`, `_deserialize_job`, `_serialize_capabilities`, `_deserialize_capabilities`, `_deserialize_worker` — redis.py:30-270) via json.dumps/loads with field-by-field reconstruction and a manual `source_type` match dispatch. cache.py serializes the same Plan/OutputFile models via pydantic `TypeAdapter` (cache.py:44,59,85,107 — `_plan_adapter.dump_json`/`validate_json`). Adding a field to PlanStep/Plan/PlanResult requires editing the redis ser site and the deser site separately, while cache.py adapts automatically. The manual deser re-derives `source_type` via a match on AudioRequest/EpubRequest, duplicating planner logic rather than persisting it. The CORR-008 and DATA-002 fixes added CacheCorruptedError wrapping and PlanStatus enum serialization, but the fundamental duplication remains — redis.py is still 403 lines.

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
  commit: pending
  date: 2026-06-20
fixed_in: ["pending"]
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

### MAINT-004 — _stage return value in StreamingExecutor is dead code — computed cost returned as float|None but never consumed by _run_pipeline

```yaml
status: verified
severity: low
effort: S
reviewed_at: d0b739b
last_verified_at:
  commit: pending
  date: 2026-06-20
fixed_in: ["pending"]
files:
  - path: src/acheron/shell/executors/streaming.py
    lines: 180-237
  - path: src/acheron/shell/executors/streaming.py
    lines: 67-98
related: [CORR-008]
```

**Issue.** `_stage` (streaming.py:180-237) returns `float | None` (cost or None) and its docstring (line 189) says "Returns the step's cost (including on FAILED), None if no work ran." However, `_run_pipeline` (streaming.py:67-98) calls `task.result()` on each stage task without capturing the return value (line 95: `task.result()` — no assignment). Costs are instead read from the shared `stage_costs` list parameter. The return value of `_stage` is computed at line 237 (`return stage_costs[stage_index]`) but silently dropped.

**Why it matters.** A misleading type signature and docstring wastes reader effort tracing data flow that doesn't exist. The dead return value survived the CORR-008 fix because the migration from per-task return to shared list was incomplete — it captured cost into the list but forgot to remove the return path.

**Recommendation.** Change `_stage` return type to `None`, remove the `return stage_costs[stage_index]` line, and update the docstring to say "Records cost in stage_costs[stage_index]." Three-line fix.

**Verification.** `just test`; `just type-check`; grep `def _stage` confirms no return type annotation.

## EXC — Exception discipline

**Grade:** A

EXC-001 (medium): `tenacity` remains an unused production dependency, and `WorkerTimeoutError`/`PlanValidationError` are still never raised — while transient network calls have no retry. EXC-002 (low): two `except Exception` sites could name narrower types (chunking.py:28 catches NLTK failures broadly; cache.py:60,108 catch pydantic validation broadly), though all boundary catches wrap and log rather than silently swallow.

### EXC-001 — tenacity dependency is unused; WorkerTimeoutError/PlanValidationError are never raised; transient network calls have no retry

```yaml
status: open
severity: medium
effort: M
reviewed_at: 23c29e1
last_verified_at:
  commit: pending
  date: 2026-06-20
fixed_in: []
files:
  - path: pyproject.toml
    lines: 20
  - path: src/acheron/core/errors.py
    lines: 16-29
  - path: src/acheron/shell/executors/streaming.py
    lines: 220-223
  - path: src/acheron/shell/transports/grpc.py
    lines: 60-75
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
status: open
severity: low
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: pending
  date: 2026-06-20
fixed_in: []
files:
  - path: src/acheron/core/chunking.py
    lines: 28
  - path: src/acheron/shell/cache.py
    lines: 60
  - path: src/acheron/shell/cache.py
    lines: 108
  - path: src/acheron/shell/orchestrator.py
    lines: 115-116
  - path: src/acheron/shell/orchestrator.py
    lines: 226-238
  - path: src/acheron/shell/executors/streaming.py
    lines: 225
  - path: src/acheron/shell/executors/streaming.py
    lines: 239
related: []
```

**Issue.** There are 7 `except Exception` sites in src. Most are defensible task/stage boundaries that wrap into domain errors and log: orchestrator.close (orchestrator.py:116, exception-isolated teardown per docstring), orchestrator._execute (orchestrator.py:216, top-level task boundary → status='failed'), streaming._stage (streaming.py:218,232 → PipelineError). Two could be narrower: chunking.py:28 wraps `nltk.sent_tokenize` in `ChunkingError` but NLTK's common failure is `LookupError` (missing punkt data) — naming it would distinguish missing-data from genuine parse failures; cache.py:60,108 wrap pydantic validation in `CacheCorruptedError` where `pydantic.ValidationError` is the expected type and a broad catch could mask an unrelated OSError or AttributeError bug in the adapter call.

**Why it matters.** Narrower exceptions at these two sites would prevent masking programming errors (e.g., AttributeError from a refactoring bug) as "corruption." Low — the current behavior is correct for the expected cases and all sites log/wrap rather than silently swallow.

**Recommendation.** Narrow chunking.py:28 to `(LookupError, OSError, ...)` as appropriate; narrow cache.py:60,108 to `pydantic.ValidationError` (chaining preserves the original). Leave the orchestrator/streaming boundary catches as-is.

**Verification.** `just test`, `just type-check`. Confirm a missing-punkt scenario still raises ChunkingError and a malformed manifest still raises CacheCorruptedError.

## TYPE — Type safety

**Grade:** A

TYPE-002 is now verified (PlanStatus enum at ad78be4). TYPE-001 (medium) persists for the AcheronClient `dict[str, Any]` return type consumed via magic-string keys in the CLI; the metadata contract sub-issue (`dict[str, object]` → `dict[str, JsonValue]`) is resolved across registry.py, stores/base.py, and schemas.py.

### TYPE-001 — AcheronClient returns dict[str, Any] consumed via magic-string keys; metadata contracts partially resolved

```yaml
status: open
severity: medium
effort: M
reviewed_at: 23c29e1
last_verified_at:
  commit: d0b739b
  date: 2026-06-20
fixed_in: []
files:
  - path: src/acheron/api_client.py
    lines: 41-110
  - path: src/acheron/cli.py
    lines: 169-254
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
