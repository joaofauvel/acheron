# Layer 9a — Streaming Pipeline Executor

## Goal

Replace `BatchAsyncExecutor` with a `StreamingExecutor` that runs the plan as a per-stage `asyncio.Queue` pipeline. Stages backpressure on the queues, run inside an outer `asyncio.TaskGroup` so a single failure cancels all stages cleanly, and each stage's worker dispatch is wrapped in `asyncio.wait_for()` for a per-step timeout. Outputs are persisted to a now-async `StepCache` after each step completes; `PlanResult.outputs` is built by scanning the cache at the end.

## Scope

In scope:
- `src/acheron/core/errors.py` — add `PipelineError(AcheronError)`.
- `src/acheron/core/models.py` — add `ExecutorStrategy.STREAMING`.
- `src/acheron/shell/cache.py` — convert `StepCache.save_outputs`, `load_outputs`, `step_has_valid_cache` to `async def` (use `aiofiles`).
- `src/acheron/shell/executors/streaming.py` — new `StreamingExecutor`.
- `src/acheron/shell/executors/__init__.py` — register `STREAMING` in the factory.
- `src/acheron/shell/api/schemas.py` — change `executor_strategy` default to `"streaming"`.
- `src/acheron/api_client.py` — change default to `"streaming"`.
- `tests/shell/test_cache.py` — convert `TestStepCache` to async, await all calls.
- `tests/shell/test_streaming_executor.py` — new, mock-based tests.
- `tests/shell/test_errors.py` (or equivalent) — verify `PipelineError` placement.
- `pyproject.toml` — add `aiofiles` dependency.

Out of scope (deferred to future layers):
- Per-chapter parallelism within a single plan (plans currently have 4-5 stages, not per-chapter structure).
- `acheron resume` command (the spec calls StepCache "resumability foundation"; actual resume is a later layer).
- Removing `BatchAsyncExecutor` — it stays as an opt-in strategy.
- Multi-queue backpressure for plans with parallel branches (current plans are linear; the dependency DAG could in theory branch but the planner doesn't produce that today).

## Architecture

### Per-stage pipeline

For a plan with N stages, the executor builds N+1 coroutines and N bounded `asyncio.Queue`s. The first stage has no upstream queue (it dispatches the first step directly); the last stage's result is the `PlanResult`'s source. A `None` sentinel on a queue tells a stage consumer to drain and exit.

```
                ┌─ stage_0(step_0) ─┐
                │   ↑               │  ← asyncio.Queue(maxsize=4)
                │   no upstream     ▼
                │              ┌─ stage_1(step_1) ─┐
                │              │   ↑               │  ← asyncio.Queue(maxsize=4)
                │              │   upstream        ▼
                │              │              ┌─ stage_2(step_2) ─┐
                │              │              │                   │
                │              │              │                   ▼
                │              │              └─ final_queue (size 1)
                ▼              ▼              ▼
        final_queue drained by run() → PlanResult
```

For typical plans (extract → chunk → translate? → synthesize → package), the pipeline is 4-5 stages. The bounded queue is the backpressure mechanism: if `synthesize` (TTS) is slow, `translate`'s `await queue.put()` blocks naturally without spinning.

### Outer TaskGroup

All stage coroutines run inside a single outer `asyncio.TaskGroup`. If any stage raises, the group's natural cancellation propagates to siblings, the `None`-sentinel protocol drains remaining queues, and `run()` catches the resulting `BaseExceptionGroup`, filters for `AcheronError` (sibling `CancelledError`s are ignored), and converts the first match to `PlanResult(status="failed", errors=[str(err)])`. If no `AcheronError` is present, the first non-`CancelledError` is wrapped as `PipelineError`.

### Per-step timeout

Each stage consumer wraps its handler dispatch in `asyncio.wait_for(handler(step, plan), timeout=step_timeout_seconds)`. A `TimeoutError` is caught, wrapped as `WorkerError("step <id> timed out after Ns") from exc`, and re-raised so the TaskGroup cancels the rest.

### Sentinel protocol

When a stage's enclosing task is cancelled (or a stage consumer exits cleanly after its step completes), its `finally` block puts a `None` on its downstream queue. Downstream stages consume `None` as the "no more work" signal and exit. The final stage's `None` signals the `run()` coroutine to break out of its result-collection loop.

This avoids the alternative of `asyncio.Queue.join()` + task cancellation, which is racy when stages have different lifetimes.

### Output collection via cache

Each successful stage calls `await self._cache.save_outputs(job_id, step_id, result.outputs)` after dispatch. Outputs are written to disk before the next stage reads from the queue, so a process crash mid-run leaves the cache in a state that future resume logic can use.

`PlanResult.outputs` is built at the end by iterating the plan's steps and calling `await self._cache.load_outputs(job_id, step_id)` for each successful step. This means `PlanResult.outputs` is always sourced from disk, not from in-memory accumulation — even for a successful run.

## StepCache async migration

Current `StepCache` is sync (`pathlib.Path` + `read_text`/`write_text`). Converting to `async def` with `aiofiles`:

```python
class StepCache:
    async def save_outputs(self, job_id: str, step_id: str, outputs: tuple[OutputFile, ...]) -> None:
        step_dir = self._data_dir / job_id / step_id
        step_dir.mkdir(parents=True, exist_ok=True)
        manifest_file = step_dir / "manifest.json"
        manifest = _output_adapter.dump_json(outputs, indent=2)
        async with aiofiles.open(manifest_file, "wb") as f:
            await f.write(manifest)

    async def load_outputs(self, job_id: str, step_id: str) -> tuple[OutputFile, ...]:
        manifest_file = self._data_dir / job_id / step_id / "manifest.json"
        if not manifest_file.exists():
            raise CacheMissError(...)
        try:
            async with aiofiles.open(manifest_file, "rb") as f:
                blob = await f.read()
            return _output_adapter.validate_json(blob)
        except Exception as exc:
            raise CacheCorruptedError(...) from exc

    async def step_has_valid_cache(self, job_id: str, step_id: str) -> bool:
        manifest_file = self._data_dir / job_id / step_id / "manifest.json"
        if not manifest_file.exists():
            return False
        try:
            outputs = await self.load_outputs(job_id, step_id)
        except (CacheMissError, CacheCorruptedError, OSError):
            return False
        for output in outputs:
            file_path = Path(output.path)
            if not file_path.exists():
                return False
            if await asyncio.to_thread(_checksum, file_path) != output.checksum:
                return False
        return True
```

The checksum on a (potentially large) audio file is the only blocking operation, so we wrap it in `asyncio.to_thread`. Manifest reads/writes are small JSON and pure async.

`PlanCache` (the plan-level cache) is unchanged — it's only used for plan compilation, not for hot-path dispatch.

## Failure & cancellation semantics

The spec's all-or-nothing model maps to:
- A stage dispatch failure (`WorkerError`, timeout, no worker) → outer TaskGroup cancels siblings.
- A `CacheCorruptedError` or unexpected `OSError` from `save_outputs` → wrapped in `PipelineError("save_outputs failed for step {id}") from exc`, raised, TaskGroup cancels siblings.
- An unexpected bare `Exception` from the handler → wrapped in `PipelineError("unexpected failure in stage {id}: {Type}") from exc`, raised, TaskGroup cancels siblings.
- A `CancelledError` (from outer cancel) → each stage's `finally` puts a `None` sentinel on its downstream queue; remaining stages drain and exit.

`run()` catches `BaseExceptionGroup`, filters for `AcheronError` (sibling `CancelledError`s are ignored), and builds a `PlanResult(status="failed", errors=[str(err)])`. If no `AcheronError` is present, the first non-`CancelledError` is wrapped as `PipelineError("streaming failure: {inner}")` with `__cause__` set to the inner.

A successful run produces `status="completed"` with `completed_steps=len(steps)`. A failed run produces `status="failed"` with `completed_steps` set to the number of stages whose manifest was readable from `StepCache` (not 0 on partial-success). The "partial" status is not used in 9a; future multi-branch plans may revisit.

## Default strategy change

`src/acheron/shell/api/schemas.py:15` and `src/acheron/api_client.py:27` both default to `"batch_async"`. Change to `"streaming"`. `BatchAsyncExecutor` remains in the codebase and the factory; existing users who pass an explicit `batch_async` keep working.

The CLI passes the strategy explicitly via `--executor`, so no CLI default change is needed.

## Exception wrapping

| Source | Wrapped as |
|---|---|
| `asyncio.TimeoutError` from `wait_for` | `WorkerError("step {id} timed out after {N}s") from exc` |
| `WorkerError` (no worker, dispatch failure) | unchanged (already in hierarchy) |
| `CacheCorruptedError` / `OSError` from `save_outputs` | `PipelineError("save_outputs failed for step {id}") from exc` |
| Unexpected `Exception` in a stage | `PipelineError("unexpected failure in stage {id}: {type(exc).__name__}") from exc` |
| Sentinel protocol violation (e.g., `None` arrives mid-stream) | TODO(branchy-future): not currently raised. The `_stage` exits cleanly on `_END` regardless of whether it was expected. Branchy plans would require distinguishing expected drain from premature termination. |

`WorkerError` continues to cover all worker-dispatch failures; `PipelineError` covers the streaming executor's internal invariants (cache, sentinel, unexpected exceptions).

## File map

| File | Change |
|---|---|
| `src/acheron/core/errors.py` | Add `PipelineError(AcheronError)` |
| `src/acheron/core/models.py` | Add `ExecutorStrategy.STREAMING = "streaming"` |
| `src/acheron/shell/cache.py` | `StepCache.save_outputs`/`load_outputs`/`step_has_valid_cache` → `async def` (aiofiles); add `data_dir` property |
| `src/acheron/shell/executors/streaming.py` | New — `StreamingExecutor` |
| `src/acheron/shell/executors/__init__.py` | Add `STREAMING` to factory (factory gains `step_cache` kwarg) |
| `src/acheron/shell/orchestrator.py` | Construct `_step_cache = StepCache(cache.data_dir)`; pass `step_cache=self._step_cache` to `create_executor`; `close()` docstring notes callers must `shutdown()` first |
| `src/acheron/shell/api/schemas.py` | `executor_strategy: str = "streaming"` |
| `src/acheron/api_client.py` | `executor_strategy: str = "streaming"` |
| `pyproject.toml` | Add `aiofiles~=24`; dev-dep `types-aiofiles~=24` for mypy |
| `tests/shell/test_cache.py` | Convert `TestStepCache` to async; await all calls |
| `tests/shell/test_streaming_executor.py` | New — mock-based tests (see Test plan below) |
| `tests/core/test_errors.py` | Add `PipelineError` placement test |

## Test plan

`tests/shell/test_streaming_executor.py` uses a mock `StepHandler` that returns a configurable `JobResult` or raises. Test cases:

- `TestNormalCompletion::test_three_step_plan_completes` — a 3-step linear plan runs to completion; `PlanResult.status == "completed"`, all 3 outputs returned, 3 step manifests written.
- `TestStepTimeout::test_slow_handler_raises_worker_error` — handler sleeps > `step_timeout`; executor raises `WorkerError("step {id} timed out after Ns")`, result is `failed`.
- `TestWorkerError::test_worker_unavailable_propagates` — handler raises `WorkerUnavailableError`; result is `failed`, error in `PlanResult.errors`.
- `TestUnexpectedException::test_unhandled_exception_wrapped_as_pipeline_error` — handler raises `RuntimeError("boom")`; stage wraps as `PipelineError("unexpected failure in stage {id}: RuntimeError")`.
- `TestCacheFailure::test_save_outputs_failure_wrapped_as_pipeline_error` — `step_cache.save_outputs` is monkey-patched to raise `OSError`; stage wraps as `PipelineError("save_outputs failed for step {id}")`.
- `TestSentinelDrain::test_sentinel_propagates_downstream` — first stage raises; downstream stages exit via the `None` sentinel (handler never called).
- `TestCostAccumulation::test_total_cost_sums_step_metrics` — three steps with `cost_estimate=0.5` → `result.total_cost == 1.5`.
- `TestCompletedStepsCount::test_completed_steps_counts_successful_only` — last stage fails after two succeed; `result.completed_steps == 2`, `result.status == "failed"`.
- `TestCacheCorruptionTolerantLoad::test_corrupt_manifest_load_returns_zero_outputs` — corrupt manifest on disk is silently skipped, executor does not crash.
- `TestOutputsFromCache::test_outputs_sourced_from_cache_not_in_memory` — patches `step_cache.load_outputs` with a decoy; result must reflect decoy (proving cache is the source, not in-memory handler returns).

`TestTaskGroupCancellation` is a placeholder class: in a linear pipeline, a stage in the middle of a handler dispatch cannot be cancelled (the failing stage is necessarily the latest to start dispatching, since the queue serializes them). The only observable cancellation is at the await boundary, covered by `TestSentinelDrain`. Branchy plans would change this.

`tests/shell/test_cache.py::TestStepCache` becomes async; all calls awaited. Coverage is preserved.

`tests/core/test_errors.py` (the actual path) gets one test: `PipelineError` is a subclass of `AcheronError` and not of `WorkerError` (preserves the spec's hierarchy separation).

## Validation

`just validate` — lint, mypy, basedpyright, full test suite, 80% coverage floor.
