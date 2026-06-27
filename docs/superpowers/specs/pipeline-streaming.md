# Acheron — Layer 9: Pipeline Streaming & Async Redis

**Pipeline-Level Streaming, Bounded Memory, and Non-Blocking Redis I/O**

## Overview

Layer 9 replaces the wave-based batch executor with a streaming pipeline and migrates Redis stores from the synchronous `redis.Redis` client to `redis.asyncio.Redis`. Together these eliminate event-loop blocking on the hot dispatch path and ensure chunk results flow from stage to stage in real time without accumulating in orchestrator memory.

Two independent, sequenced sub-projects:

- **9a — Streaming Pipeline Executor**: per-stage `asyncio.Queue` pipeline, replacing `BatchAsyncExecutor` as the default for new jobs. Per-step `asyncio.wait_for()` timeout, outer `asyncio.TaskGroup` for clean cancellation, `None` sentinel protocol for stage drainage, `StepCache.save_outputs()` per chunk, `PlanResult.outputs` built by cache scan. See `docs/superpowers/specs/layer-9a-streaming-executor.md` for full design.
- **9b — Async Redis Stores**: `WorkerStore` and `JobStore` ABCs migrate to `async def`; Redis backends switch to `redis.asyncio.Redis`.

9b is a prerequisite for 9a in production: without async Redis, `registry.list_all()` in the hot dispatch loop still blocks the event loop. Both sub-projects are independently testable and land in sequence.

## Architecture

### What Changes

| Component | Before | After |
|---|---|---|
| Executor (GPU jobs) | `BatchAsyncExecutor` — wave-based `asyncio.gather` | `StreamingExecutor` — per-chapter `asyncio.Queue` pipeline |
| Store ABCs | Sync `def` methods | `async def` methods |
| Redis backends | `redis.Redis` (sync, blocks event loop) | `redis.asyncio.Redis` (non-blocking) |
| Step timeout | Not enforced | `asyncio.wait_for()` per dispatch, configurable |
| Error wrapping | Raw exceptions may escape stage | All exceptions wrapped in `AcheronError` subclass before propagating |
| Output collection | `list[OutputFile]` accumulated in executor memory | `StepCache.save_outputs()` per chunk; `PlanResult.outputs` from cache scan |

### What Stays the Same

- `Plan` / `PlanStep` / `PlanResult` — unchanged.
- `StepHandler` type alias — unchanged.
- `Worker` / `StreamingWorker` ABCs — unchanged.
- `PlanCache` / `StepCache` on-disk format — unchanged.
- `SequentialExecutor` and `AsyncExecutor` — unchanged, for debugging and non-GPU jobs.

## Sub-project 9a — Streaming Pipeline Executor

### Stage Model

Today's plans have a linear topology (extract → chunk → translate? → synthesize → package). The executor runs each plan as a single chain of stage coroutines connected by bounded `asyncio.Queue`s. Per-chapter parallelism within a single plan is a future enhancement (plans gain per-chapter structure); the current implementation handles the linear case and generalises naturally to branches when the planner produces them.

```
chunks (PlanSteps, iterated directly)
    │
stage_0 (extract)   ← await wait_for(handler(step, plan), timeout)
    │
    ▼  asyncio.Queue(maxsize=queue_size)
stage_1 (chunk)     ← await wait_for(handler(step, plan), timeout)
    │
    ▼  asyncio.Queue(maxsize=queue_size)
stage_2 (synthesize) ← await wait_for(handler(step, plan), timeout)
    │
    ▼  asyncio.Queue(maxsize=queue_size)
stage_3 (package)   ← await StepCache.save_outputs() per chunk
```

The first stage has no upstream queue — it dispatches the first step directly. Subsequent stages consume from the previous stage's queue. All stages run concurrently in a single outer `asyncio.TaskGroup`.

### Data Flow

```
submit_job()
  │
  ├── compile_plan() → Plan
  │
  └── asyncio.TaskGroup (outer — all stages of the plan)
        ├── stage_0 (extract)   → Queue → puts JobResult
        ├── stage_1 (chunk)     → Queue → puts JobResult
        ├── stage_2 (synthesize) → Queue → puts JobResult
        └── stage_3 (package)   → StepCache.save_outputs() [disk]

  On success → PlanResult(status="completed", outputs from StepCache scan,
                          total_cost from sum of cost_estimate,
                          completed_steps = len(steps))
  On any failure → outer TaskGroup cancels all sibling stages immediately
                 → PlanResult(status="failed", errors=[...],
                              completed_steps = count of steps that wrote a manifest,
                              outputs = whatever was on disk for those steps)
```

### Failure Semantics

An audiobook is all-or-nothing: if any stage fails, the job is aborted and all running stages are cancelled immediately to avoid wasting GPU resources.

The outer `asyncio.TaskGroup`'s natural cancellation behaviour handles this: when a stage task raises, the group cancels all sibling stage tasks. `StreamingExecutor.run()` catches `BaseExceptionGroup`, filters for `AcheronError` (sibling `CancelledError`s are ignored), and converts the first match to `PlanResult(status="failed", errors=[str(err)])`. If no `AcheronError` is present, the first non-`CancelledError` is wrapped as `PipelineError`.

In a linear pipeline, a stage in the middle of a handler dispatch cannot be cancelled — the failing stage is necessarily the latest to start dispatching (the queue serializes them). The cancellation manifests as downstream stages' `await upstream.get()` being interrupted by `CancelledError`. Branchy plans would change this.

**Exception wrapping:** All raw exceptions at stage boundaries are wrapped in the Acheron error hierarchy before re-raising, preserving the full chain with `raise X from exc`:

- `TimeoutError` from `asyncio.wait_for()` → `WorkerError("step <id> timed out after Ns") from exc`
- No registered worker → already raises `WorkerError` (existing behaviour, unchanged)
- Transport failure after tenacity exhausts retries → `WorkerError("worker failed: ...") from exc`
- `CacheCorruptedError` / `OSError` from `save_outputs` → `PipelineError("save_outputs failed for step <id>") from exc`
- Unexpected stage exception → `PipelineError("unexpected failure in stage <id>: <Type>") from exc`

**`None` sentinel on cancellation:** Every stage's `finally` block puts a `None` sentinel on its downstream queue (clean exit or cancel). Downstream stages consume `None` as the "no more work" signal and exit. The final stage's sentinel signals `run()` to break out of its result-collection loop. The plan's first stage is special: it has no upstream queue, so it dispatches immediately.

**Retry policy:** No retry at the executor level. Transport-level retry (tenacity in `HttpWorker`) already exhausts retries before propagating failure. A failed `JobResult` or raised exception at the stage is treated as final.

**Sentinel protocol violation check** (e.g. `None` arriving mid-stream) is logged as a TODO in the current code: in the linear topology, the second case cannot arise. Branchy plans would require the check.

### Memory Behaviour

At peak, the orchestrator holds at most `maxsize × (num_stages − 1)` `JobResult` objects in queues. `JobResult` is metadata only (~300 bytes — path, checksum, size); audio bytes are never in orchestrator memory. For a 5-stage plan with `maxsize=4`, that's 16 `JobResult` objects ≈ 5 KB.

`PlanResult.outputs` is populated by scanning `StepCache` at job completion, not by accumulating `OutputFile` objects during the run. `total_cost` is the sum of `result.metrics.cost_estimate` for each stage whose handler returned successfully. `completed_steps` reflects the count of steps whose manifest was readable (not 0 on partial-success).

### Backpressure

`asyncio.Queue(maxsize=queue_size)` blocks `await queue.put()` when the downstream stage is slower. A slow synthesize (TTS) stage naturally stalls the translate stage at the queue boundary — no polling, no sleep, no wasted CPU.

Default `queue_size` is 4. Configurable per `StreamingExecutor` instance.

### Step Timeout

`asyncio.wait_for(handler(step, plan), timeout=step_timeout_seconds)` wraps every worker dispatch inside a stage consumer. Default: 1800s. Configurable per `StreamingExecutor` instance.

`TimeoutError` is caught, wrapped as `WorkerError`, and re-raised. The outer `TaskGroup` then cancels the other stage coroutines via its natural failure propagation.

### Resumability Foundation

`StepCache.save_outputs()` is called after each successful step, as it completes — not deferred to wave completion. This gives finer-grained checkpoints than the current wave-based model. When `acheron resume` is implemented (out of scope for this layer), the executor reads `StepCache` to identify completed steps and skips their dispatch. No new infrastructure is required: the checkpoint skeleton already exists in `StepCache` (now async via aiofiles for this layer).

### Executor Strategy

Add `ExecutorStrategy.STREAMING` to the enum. `create_executor()` extended to produce `StreamingExecutor` for this strategy. The factory signature gains a `step_cache: StepCache | None = None` keyword arg; `STREAMING` requires it (raises `ValueError` otherwise), the other strategies ignore it. `STREAMING` is the new default for the API (`api/schemas.py`) and client (`api_client.py`); `BATCH_ASYNC` remains in the codebase and the factory, and users who pass it explicitly keep working.

## Sub-project 9b — Async Redis Stores

### Store ABC Migration

All methods on `WorkerStore` and `JobStore` (in `shell/stores/base.py`) become `async def`. `close()` also becomes `async def` on Redis backends (since `aio.Redis.aclose()` is a coroutine); `Orchestrator.close()` is already `async def` so it awaits naturally.

**`InMemoryWorkerStore` / `InMemoryJobStore`:** Trivially `async def` — no I/O, no internal `await`s. Behaviour unchanged.

**`RedisWorkerStore` / `RedisJobStore`:** Switch from `redis.Redis` to `redis.asyncio.Redis`. All `pipe.execute()` become `await pipe.execute()`. `__init__` does no I/O; connectivity is verified via a new `async def connect()` instance method that `await`s `self._redis.ping()`. The store ABCs expose a concrete `connect()` with a no-op default; `Orchestrator.start()` awaits `connect()` on both stores. `close()` becomes `async def` and calls `await self._redis.aclose()`. See `docs/superpowers/specs/layer-9b-ii-redis-async.md` for full design.

No new dependency: `redis.asyncio` is part of the existing `redis~=7.0` package.

### Call Site Updates

| File | Change |
|---|---|
| `shell/health.py` | `await registry.list_all()`, `await registry.record_health_success/failure()` |
| `shell/step_handler.py` | `await registry.list_all()` |
| `shell/orchestrator.py` | `register_worker`, `list_workers`, `get_capabilities` → `async def`; `close()` → `await` stores |

### Sub-project Split

- **9b-i — Store ABC + InMemory async:** ABCs → `async def`, InMemory backends updated, all call sites `await`. Superseded by 9b-ii (the 9b-i cycle collapsed into the 9b-ii spec when the migration scope was scoped down to the Redis backend; ABC changes landed as part of 9b-ii).
- **9b-ii — Redis async backend:** Done. `__init__` does no I/O; concrete `async def connect()` on the ABCs (no-op default); Redis stores override to `await self._redis.ping()`. `Orchestrator.start()` awaits `connect()` on both stores. `close()` → `aclose()`. See `docs/superpowers/specs/layer-9b-ii-redis-async.md`.
- **9a — Streaming pipeline executor:** Per-stage `asyncio.Queue` pipeline. Per-step `asyncio.wait_for()` timeout, outer `asyncio.TaskGroup`, `None` sentinel drainage. `StepCache` becomes async via aiofiles. `STREAMING` is the new default strategy. All-or-nothing failure semantics: any stage failure → outer TaskGroup cancels siblings → `PlanResult.status == "failed"`. See `docs/superpowers/specs/layer-9a-streaming-executor.md`.
- **9b-ii — Redis async backend:** Swap `redis.Redis` → `redis.asyncio.Redis` in both Redis stores. `__init__` does no I/O; a concrete `async def connect()` is added to the store ABCs (no-op default) and overridden by Redis stores to `await self._redis.ping()`. `Orchestrator.start()` awaits `connect()` on both stores. `close()` becomes `async def` and calls `await self._redis.aclose()`. See `docs/superpowers/specs/layer-9b-ii-redis-async.md` for full design.

## New Error Type

`PipelineError(AcheronError)` — added to `core/errors.py`. Represents unexpected failures during streaming execution that are not worker-dispatch errors (e.g. internal stage wiring bugs, sentinel protocol violations). `WorkerError` continues to cover all dispatch-level failures.

## File Map

| File | Change |
|---|---|
| `core/errors.py` | Add `PipelineError(AcheronError)` |
| `core/models.py` | Add `ExecutorStrategy.STREAMING` |
| `shell/stores/base.py` | All methods → `async def`; `close()` → `async def` |
| `shell/stores/memory.py` | All methods → `async def` (trivial) |
| `shell/stores/redis.py` | All methods → `async def`; `redis.asyncio.Redis`; `connect()` instance method (called from `Orchestrator.start()`); `close()` → `async def` (`aclose()`) |
| `shell/cache.py` | (new in 9a) `StepCache.save_outputs` / `load_outputs` / `step_has_valid_cache` → `async def` via aiofiles; `data_dir` property added |
| `shell/executors/streaming.py` | New — `StreamingExecutor` |
| `shell/executors/__init__.py` | Add `STREAMING` to factory (factory takes `step_cache` kwarg) |
| `shell/orchestrator.py` | Construct `_step_cache = StepCache(cache.data_dir)`; pass `step_cache=self._step_cache` to `create_executor`; `close()` docstring notes callers must `shutdown()` first |
| `shell/api/schemas.py` | Default `executor_strategy` → `"streaming"` |
| `api_client.py` | Default `executor_strategy` → `"streaming"` |
| `pyproject.toml` | Add `aiofiles~=24`; dev-dep `types-aiofiles~=24` for mypy |
| `tests/shell/test_streaming_executor.py` | New — mock-based tests for normal completion, step timeout, no worker, unexpected exception, cache save failure, sentinel drain, cost accumulation, completed_steps count, cache-sourced outputs, cache-corruption tolerance |
| `tests/shell/test_cache.py` | Convert `TestStepCache` to async (await all calls) |
| `tests/core/test_errors.py` | Add `PipelineError` placement test |

## Testing Strategy

**Unit tests (no Redis, no workers):**

- `test_streaming_executor.py` — mock `StepHandler` covering: normal completion, step timeout → `WorkerError`, no worker → `WorkerError`, unexpected exception → `PipelineError`, outer TaskGroup cancels all chapters on single-chapter failure, sentinel drain on `CancelledError`.
- `test_stores_async.py` — `InMemoryWorkerStore` / `InMemoryJobStore` async method contracts.
- `test_errors.py` extension — `PipelineError` placement in hierarchy.

**Integration tests (testcontainers Redis):**

- `test_redis_stores_async.py` — `RedisWorkerStore` / `RedisJobStore` with `redis.asyncio.Redis`; full CRUD and health recording round-trips.

## Roadmap

This is Layer 9, decomposed as:

| Sub-project | Scope | Prerequisite |
|---|---|---|
| 9b-i | Store ABC + InMemory async | Layer 7a |
| 9b-ii | Redis async backend | 9b-i |
| 9a | Streaming pipeline executor | 9b |
