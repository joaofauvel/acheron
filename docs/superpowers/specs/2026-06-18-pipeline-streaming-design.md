# Acheron — Layer 9: Pipeline Streaming & Async Redis

**Pipeline-Level Streaming, Bounded Memory, and Non-Blocking Redis I/O**

## Overview

Layer 9 replaces the wave-based batch executor with a streaming pipeline and migrates Redis stores from the synchronous `redis.Redis` client to `redis.asyncio.Redis`. Together these eliminate event-loop blocking on the hot dispatch path and ensure chunk results flow from stage to stage in real time without accumulating in orchestrator memory.

Two independent, sequenced sub-projects:

- **9a — Streaming Pipeline Executor**: chunk-by-chunk pipeline per chapter via `asyncio.Queue`, replacing `BatchAsyncExecutor` as the default for GPU jobs.
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

Each chapter is an independent pipeline of stage coroutines connected by bounded `asyncio.Queue`s:

```
chunks (PlanSteps)
    │
    ▼  asyncio.Queue(maxsize=queue_size)
translate_stage   ← await wait_for(handler(step, plan), timeout)
    │
    ▼  asyncio.Queue(maxsize=queue_size)
tts_stage         ← await wait_for(handler(step, plan), timeout)
    │
    ▼  asyncio.Queue(maxsize=queue_size)
package_stage     ← StepCache.save_outputs() per chunk
```

All chapters run concurrently in an outer `asyncio.TaskGroup`.

### Data Flow

```
submit_job()
  │
  ├── compile_plan() → Plan
  │
  └── asyncio.TaskGroup (outer — all chapters)
        ├── chapter_pipeline("ch1")
        │     inner asyncio.TaskGroup
        │       ├── translate_stage  → Queue → puts JobResult
        │       ├── tts_stage        → Queue → puts JobResult
        │       └── package_stage    → StepCache.save_outputs() [disk]
        │
        ├── chapter_pipeline("ch2")  [concurrent]
        └── chapter_pipeline("ch3")  [concurrent]

  On success → PlanResult(status="completed", outputs from StepCache scan)
  On any failure → outer TaskGroup cancels all chapters immediately
                 → PlanResult(status="failed", errors=[...])
```

### Failure Semantics

An audiobook is all-or-nothing: if any chapter fails, the job is aborted and all running chapters are cancelled immediately to avoid wasting GPU resources.

The outer `asyncio.TaskGroup`'s natural cancellation behaviour handles this: when a chapter task raises, the group cancels all sibling chapter tasks. `StreamingExecutor.run()` catches `ExceptionGroup[AcheronError]` and converts it to `PlanResult(status="failed", errors=[...])`.

Within a chapter, the inner `asyncio.TaskGroup` cancels sibling stage coroutines when any one stage fails, ensuring the chapter's pipeline drains cleanly.

**Exception wrapping:** All raw exceptions at stage boundaries are wrapped in the Acheron error hierarchy before re-raising, preserving the full chain with `raise X from exc`:

- `TimeoutError` from `asyncio.wait_for()` → `WorkerError("step timed out after Ns") from exc`
- No registered worker → already raises `WorkerError` (existing behaviour, unchanged)
- Transport failure after tenacity exhausts retries → `WorkerError("worker failed: ...") from exc`
- Unexpected stage failures → `PipelineError("unexpected failure in stage X") from exc`

**`None` sentinel on cancellation:** When a `CancelledError` arrives, each stage consumer's `finally` block puts a `None` sentinel to its downstream queue, allowing remaining stage coroutines in that chapter to drain and exit cleanly without goroutine leaks.

**Retry policy:** No retry at the executor level. Transport-level retry (tenacity in `HttpWorker`) already exhausts retries before propagating failure. A failed `JobResult` or raised exception at the stage is treated as final.

### Memory Behaviour

At peak, the orchestrator holds at most `maxsize × num_stages × num_chapters` `JobResult` objects in queues. `JobResult` is metadata only (~300 bytes — path, checksum, size); audio bytes are never in orchestrator memory. For 30 chapters × 2 queues × 4 maxsize = 240 objects ≈ 72 KB.

`PlanResult.outputs` is populated by scanning `StepCache` at job completion, not by accumulating `OutputFile` objects during the run.

### Backpressure

`asyncio.Queue(maxsize=queue_size)` blocks `await queue.put()` when the downstream stage is slower. A slow TTS stage naturally stalls the translate stage at the queue boundary — no polling, no sleep, no wasted CPU.

Default `queue_size` is 4. Configurable per `StreamingExecutor` instance.

### Step Timeout

`asyncio.wait_for(handler(step, plan), timeout=step_timeout_seconds)` wraps every worker dispatch inside a stage consumer. Default: 1800s (matching the error table in the master spec). Configurable per `StreamingExecutor` instance.

`TimeoutError` is caught, wrapped as `WorkerError`, and re-raised. The inner `asyncio.TaskGroup` then cancels the chapter's other stage coroutines via its natural failure propagation.

### Resumability Foundation

`StepCache.save_outputs()` is called after each successful step, as it completes — not deferred to wave completion. This gives finer-grained checkpoints than the current wave-based model. When `acheron resume` is implemented (out of scope for this layer), the executor reads `StepCache` to identify completed steps and skips their dispatch. No new infrastructure is required: the checkpoint skeleton already exists in `StepCache`.

### Executor Strategy

Add `ExecutorStrategy.STREAMING` to the enum. `create_executor()` extended to produce `StreamingExecutor` for this strategy. `BATCH_ASYNC` remains and continues to function — no removal.

## Sub-project 9b — Async Redis Stores

### Store ABC Migration

All methods on `WorkerStore` and `JobStore` (in `shell/stores/base.py`) become `async def`. `close()` also becomes `async def` on Redis backends (since `aio.Redis.aclose()` is a coroutine); `Orchestrator.close()` is already `async def` so it awaits naturally.

**`InMemoryWorkerStore` / `InMemoryJobStore`:** Trivially `async def` — no I/O, no internal `await`s. Behaviour unchanged.

**`RedisWorkerStore` / `RedisJobStore`:** Switch from `redis.Redis` to `redis.asyncio.Redis`. All `pipe.execute()` become `await pipe.execute()`. `__init__` cannot `await`, so connectivity is verified via a new `async def connect()` classmethod used by the factory instead of a synchronous `ping()` in `__init__`. `close()` becomes `async def` and calls `await self._redis.aclose()`.

No new dependency: `redis.asyncio` is part of the existing `redis~=7.0` package.

### Call Site Updates

| File | Change |
|---|---|
| `shell/health.py` | `await registry.list_all()`, `await registry.record_health_success/failure()` |
| `shell/step_handler.py` | `await registry.list_all()` |
| `shell/orchestrator.py` | `register_worker`, `list_workers`, `get_capabilities` → `async def`; `close()` → `await` stores |

### Sub-project Split

- **9b-i — Store ABC + InMemory async:** ABCs → `async def`, InMemory backends updated, all call sites `await`. Validates with existing unit tests (no Redis required).
- **9b-ii — Redis async backend:** Swap `redis.Redis` → `redis.asyncio.Redis` in both Redis stores. Integration tests via testcontainers (same pattern as 7a).

## New Error Type

`PipelineError(AcheronError)` — added to `core/errors.py`. Represents unexpected failures during streaming execution that are not worker-dispatch errors (e.g. internal stage wiring bugs, sentinel protocol violations). `WorkerError` continues to cover all dispatch-level failures.

## File Map

| File | Change |
|---|---|
| `core/errors.py` | Add `PipelineError(AcheronError)` |
| `core/models.py` | Add `ExecutorStrategy.STREAMING` |
| `shell/stores/base.py` | All methods → `async def`; `close()` → `async def` |
| `shell/stores/memory.py` | All methods → `async def` (trivial) |
| `shell/stores/redis.py` | All methods → `async def`; `redis.asyncio.Redis`; `connect()` classmethod; `close()` → `async def` |
| `shell/executors/streaming.py` | New — `StreamingExecutor` |
| `shell/executors/__init__.py` | Add `STREAMING` to factory |
| `shell/health.py` | Store calls → `await` |
| `shell/step_handler.py` | `registry.list_all()` → `await` |
| `shell/orchestrator.py` | `register_worker`, `list_workers`, `get_capabilities` → `async def`; `close()` awaits stores |
| `tests/shell/test_streaming_executor.py` | New |
| `tests/shell/test_stores_async.py` | New (InMemory async contracts) |
| `tests/shell/test_redis_stores_async.py` | New (testcontainers integration) |

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
