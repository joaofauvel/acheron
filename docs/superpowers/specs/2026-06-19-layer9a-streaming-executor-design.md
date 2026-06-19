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

All stage coroutines run inside an outer `asyncio.TaskGroup`. If any stage raises, the group's natural cancellation propagates to siblings, the `None`-sentinel protocol drains remaining queues, and the group's `ExceptionGroup[AcheronError]` is caught at the top of `run()`.

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
- A stage dispatch failure (WorkerError, timeout, no worker) → outer TaskGroup cancels siblings.
- A `CacheCorruptedError` or unexpected `OSError` from `save_outputs` → wrapped in `PipelineError("save_outputs failed for step X") from exc`, raised, TaskGroup cancels siblings.
- A `CancelledError` (from outer cancel) → each stage's `finally` puts a `None` sentinel on its downstream queue; remaining stages drain and exit.

`run()` catches `ExceptionGroup` (Python 3.14+ has implicit `except*` semantics via `BaseExceptionGroup`), extracts the first `AcheronError`, and builds a `PlanResult(status="failed", errors=[...])`. Other exceptions become `PipelineError("unexpected streaming failure") from exc` and produce a `failed` result.

A successful run produces `status="completed"`. A partial run (some stages succeeded, some failed) is currently impossible in the linear pipeline model — if any stage fails, all subsequent stages are cancelled. The spec mentions "partial" as a status but for a single linear pipeline it never arises. Future multi-branch plans will revisit this.

## Default strategy change

`src/acheron/shell/api/schemas.py:15` and `src/acheron/api_client.py:27` both default to `"batch_async"`. Change to `"streaming"`. `BatchAsyncExecutor` remains in the codebase and the factory; existing users who pass an explicit `batch_async` keep working.

The CLI passes the strategy explicitly via `--executor`, so no CLI default change is needed.

## Exception wrapping

| Source | Wrapped as |
|---|---|
| `asyncio.TimeoutError` from `wait_for` | `WorkerError("step {id} timed out after {N}s") from exc` |
| `WorkerError` (no worker, dispatch failure) | unchanged (already in hierarchy) |
| `CacheCorruptedError` / `OSError` from `save_outputs` | `PipelineError("save_outputs failed for step {id}") from exc` |
| Unexpected `Exception` in a stage | `PipelineError("unexpected failure in stage {id}: {type}") from exc` |
| Sentinel protocol violation (e.g., `None` arrives mid-stream) | `PipelineError("sentinel received before queue drained") from exc` |

`WorkerError` continues to cover all worker-dispatch failures; `PipelineError` covers the streaming executor's internal invariants (cache, sentinel, unexpected exceptions).

## File map

| File | Change |
|---|---|
| `src/acheron/core/errors.py` | Add `PipelineError(AcheronError)` |
| `src/acheron/core/models.py` | Add `ExecutorStrategy.STREAMING = "streaming"` |
| `src/acheron/shell/cache.py` | `StepCache.save_outputs`/`load_outputs`/`step_has_valid_cache` → `async def` (aiofiles) |
| `src/acheron/shell/executors/streaming.py` | New — `StreamingExecutor` |
| `src/acheron/shell/executors/__init__.py` | Add `STREAMING` to factory |
| `src/acheron/shell/api/schemas.py` | `executor_strategy: str = "streaming"` |
| `src/acheron/api_client.py` | `executor_strategy: str = "streaming"` |
| `pyproject.toml` | Add `aiofiles~=24` |
| `tests/shell/test_cache.py` | Convert `TestStepCache` to async; await all calls |
| `tests/shell/test_streaming_executor.py` | New — mock-based tests |
| `tests/shell/test_errors.py` | Add `PipelineError` placement test |

## Test plan

`tests/shell/test_streaming_executor.py` uses a mock `StepHandler` that returns a configurable `JobResult` or raises. The mock has spy counters so we can verify:

- `test_normal_completion` — 4-step plan runs to completion, `PlanResult.status == "completed"`, all 4 outputs returned.
- `test_step_timeout` — handler sleeps > timeout, executor raises `WorkerError`, other stages cancelled, `PlanResult.status == "failed"`.
- `test_no_worker` — handler raises `WorkerUnavailableError`, same as above.
- `test_unexpected_exception_in_stage` — handler raises `RuntimeError`, wrapped as `PipelineError`.
- `test_outer_taskgroup_cancels_all_stages_on_failure` — middle stage fails; spy shows upstream and downstream stages were cancelled (not completed).
- `test_sentinel_drain_on_cancellation` — when stage N fails, stage N+1 sees a `None` sentinel and exits cleanly.
- `test_cache_save_failure_wrapped_as_pipeline_error` — `save_outputs` raises; wrapped as `PipelineError`.
- `test_outputs_built_from_cache_scan` — even on a successful run, `PlanResult.outputs` is sourced by scanning the cache (verifiable by deleting the in-memory outputs and showing the cache is the source).

`tests/shell/test_cache.py::TestStepCache` becomes async; all calls awaited. Coverage is preserved.

`tests/shell/test_errors.py` (if it exists) gets one test: `PipelineError` is a subclass of `AcheronError` and not of `WorkerError` (preserves the spec's hierarchy separation).

## Validation

`just validate` — lint, mypy, basedpyright, full test suite, 80% coverage floor.
