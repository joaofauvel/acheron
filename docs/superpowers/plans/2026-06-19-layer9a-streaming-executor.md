# Layer 9a Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `BatchAsyncExecutor` as the default with a `StreamingExecutor` that runs the plan as a per-stage `asyncio.Queue` pipeline with bounded backpressure, per-step timeout, and clean TaskGroup-based cancellation. Make `StepCache` async via aiofiles.

**Architecture:** `StreamingExecutor.run(plan)` builds N+1 stage coroutines and N bounded `asyncio.Queue`s for the plan's N stages. All stages run in an outer `asyncio.TaskGroup`. Each stage's worker dispatch is wrapped in `asyncio.wait_for()`. Successful stages write outputs to `StepCache` (now async via aiofiles). A `None` sentinel on each queue's `finally` block lets downstream stages drain and exit on cancel. `PlanResult.outputs` is built by scanning the cache at the end.

**Tech Stack:** Python 3.14, `asyncio.Queue`, `asyncio.TaskGroup`, `asyncio.wait_for`, `asyncio.to_thread` (for cache checksum), `aiofiles~=24` (new).

---

## File Map

| File | Responsibility |
|---|---|
| `src/acheron/core/errors.py` | Add `PipelineError` |
| `src/acheron/core/models.py` | Add `ExecutorStrategy.STREAMING` |
| `src/acheron/shell/cache.py` | Convert `StepCache` to async (aiofiles) |
| `src/acheron/shell/executors/streaming.py` | New `StreamingExecutor` |
| `src/acheron/shell/executors/__init__.py` | Register `STREAMING` in factory |
| `src/acheron/shell/orchestrator.py` | Construct `_step_cache`, pass to factory for STREAMING |
| `src/acheron/shell/api/schemas.py` | Default `executor_strategy` → `"streaming"` |
| `src/acheron/api_client.py` | Default `executor_strategy` → `"streaming"` |
| `pyproject.toml` | Add `aiofiles~=24` |
| `tests/core/test_errors.py` | Add `PipelineError` placement test |
| `tests/shell/test_cache.py` | Convert `TestStepCache` to async |
| `tests/shell/test_streaming_executor.py` | New — mock-based tests |

---

## Task 1: Add `PipelineError` to the error hierarchy

**Files:**
- Modify: `src/acheron/core/errors.py`
- Modify: `tests/core/test_errors.py`

- [ ] **Step 1: Write the failing test**

In `tests/core/test_errors.py`, add a new test class:

```python
class TestPipelineError:
    def test_pipeline_error_inherits_from_acheron_error(self) -> None:
        from acheron.core.errors import PipelineError

        assert issubclass(PipelineError, AcheronError)

    def test_pipeline_error_does_not_inherit_from_worker_error(self) -> None:
        from acheron.core.errors import PipelineError, WorkerError

        assert not issubclass(PipelineError, WorkerError)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/core/test_errors.py::TestPipelineError --no-cov -v 2>&1 | tail -10`
Expected: FAIL — `ImportError: cannot import name 'PipelineError'`.

- [ ] **Step 3: Add `PipelineError` to `core/errors.py`**

In `src/acheron/core/errors.py`, append after `ChunkingError`:

```python
class PipelineError(AcheronError):
    """Unexpected failures during streaming pipeline execution.

    Reserved for executor-internal invariants (cache, sentinel protocol,
    unexpected stage failures). Worker-dispatch failures continue to be
    represented by ``WorkerError`` subclasses.
    """
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/core/test_errors.py::TestPipelineError --no-cov -v 2>&1 | tail -10`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/acheron/core/errors.py tests/core/test_errors.py
git commit -m "feat(errors): add PipelineError for streaming executor internals"
```

---

## Task 2: Add `ExecutorStrategy.STREAMING` and register in the factory

**Files:**
- Modify: `src/acheron/core/models.py`
- Modify: `src/acheron/shell/executors/__init__.py`

- [ ] **Step 1: Add the enum value**

In `src/acheron/core/models.py`, update the `ExecutorStrategy` class (line 37):

```python
class ExecutorStrategy(Enum):
    """Plan execution strategy."""

    SEQUENTIAL = "sequential"
    ASYNC = "async"
    BATCH_ASYNC = "batch_async"
    STREAMING = "streaming"
```

- [ ] **Step 2: Update the factory**

In `src/acheron/shell/executors/__init__.py`, add the import and the match arm:

```python
"""Executor implementations and factory."""

from __future__ import annotations

from typing import TYPE_CHECKING

from acheron.core.models import ExecutorStrategy
from acheron.shell.executors.async_executor import AsyncExecutor
from acheron.shell.executors.batch_async import BatchAsyncExecutor
from acheron.shell.executors.sequential import SequentialExecutor
from acheron.shell.executors.streaming import StreamingExecutor

if TYPE_CHECKING:
    from acheron.core.interfaces import Executor
    from acheron.shell.cache import StepCache
    from acheron.shell.executors._utils import StepHandler


def create_executor(
    strategy: ExecutorStrategy,
    handler: StepHandler,
    *,
    step_cache: StepCache | None = None,
) -> Executor:
    """Create an executor instance for the given strategy."""
    match strategy:
        case ExecutorStrategy.SEQUENTIAL:
            return SequentialExecutor(handler)
        case ExecutorStrategy.ASYNC:
            return AsyncExecutor(handler)
        case ExecutorStrategy.BATCH_ASYNC:
            return BatchAsyncExecutor(handler)
        case ExecutorStrategy.STREAMING:
            if step_cache is None:
                msg = "StreamingExecutor requires a step_cache"
                raise ValueError(msg)
            return StreamingExecutor(handler, step_cache)


__all__ = [
    "AsyncExecutor",
    "BatchAsyncExecutor",
    "SequentialExecutor",
    "StreamingExecutor",
    "create_executor",
]
```

- [ ] **Step 3: Run mypy to confirm the streaming import fails (expected)**

Run: `uv run mypy src/acheron/shell/executors/__init__.py 2>&1 | tail -10`
Expected: FAIL with `ModuleNotFoundError: No module named 'acheron.shell.executors.streaming'`. (We'll create the module in Task 5.)

- [ ] **Step 4: Commit (without the import working — fix in Task 5)**

```bash
git add src/acheron/core/models.py src/acheron/shell/executors/__init__.py
git commit -m "feat(executors): add STREAMING strategy and factory dispatch"
```

---

## Task 3: Add `aiofiles` dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add the dependency via uv**

Run: `uv add aiofiles~=24`
Expected: `pyproject.toml` gets `aiofiles~=24` in `dependencies`; `uv.lock` updates.

- [ ] **Step 2: Verify the package is importable**

Run: `uv run python -c "import aiofiles; print(aiofiles.__version__)"`
Expected: prints a version string (e.g., `24.1.0`).

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore(deps): add aiofiles for async StepCache I/O"
```

---

## Task 4: Convert `StepCache` to async (TDD)

**Files:**
- Modify: `src/acheron/shell/cache.py`
- Modify: `tests/shell/test_cache.py`

- [ ] **Step 1: Convert `TestStepCache` to async, await calls**

In `tests/shell/test_cache.py`, replace the entire `TestStepCache` class (lines 105-179) with an async version. Find it via the existing pattern — every test method changes from `def` to `async def`, every call to `cache.save_outputs(...)` / `cache.load_outputs(...)` / `cache.step_has_valid_cache(...)` is awaited, and the test class uses `@pytest_asyncio.fixture` where applicable. Concretely, the new class:

```python
import pytest_asyncio

class TestStepCache:
    @pytest_asyncio.fixture
    async def cache(self, tmp_path: Path) -> StepCache:
        return StepCache(tmp_path)

    @pytest.mark.asyncio
    async def test_save_and_load_outputs(self, cache: StepCache) -> None:
        outputs = (
            OutputFile(
                path=str(tmp_path := cache._data_dir / "job-1" / "tts-ch1" / "out.wav"),
                filename="out.wav",
                size_bytes=42,
                checksum="abc",
                content_type="audio/wav",
            ),
        )
        # Pre-create the file so checksum/file_exists checks pass.
        Path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
        Path(tmp_path).write_bytes(b"x" * 42)
        await cache.save_outputs("job-1", "tts-ch1", outputs)
        loaded = await cache.load_outputs("job-1", "tts-ch1")
        assert loaded == outputs

    @pytest.mark.asyncio
    async def test_load_missing_raises_cache_miss(self, cache: StepCache) -> None:
        with pytest.raises(CacheMissError):
            await cache.load_outputs("job-1", "nope")

    @pytest.mark.asyncio
    async def test_step_has_valid_cache_true(self, cache: StepCache) -> None:
        out = cache._data_dir / "out.wav"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"x" * 42)
        outputs = (OutputFile(path=str(out), filename="out.wav", size_bytes=42,
                              checksum=_checksum(out), content_type="audio/wav"),)
        await cache.save_outputs("job-1", "tts-ch1", outputs)
        assert await cache.step_has_valid_cache("job-1", "tts-ch1")

    @pytest.mark.asyncio
    async def test_step_has_valid_cache_missing_manifest(self, cache: StepCache) -> None:
        assert not await cache.step_has_valid_cache("job-1", "nope")

    @pytest.mark.asyncio
    async def test_step_has_valid_cache_corrupted_checksum(
        self, cache: StepCache, tmp_path: Path
    ) -> None:
        out = tmp_path / "out.wav"
        out.write_bytes(b"x" * 42)
        outputs = (OutputFile(path=str(out), filename="out.wav", size_bytes=42,
                              checksum="not-the-real-checksum", content_type="audio/wav"),)
        await cache.save_outputs("job-1", "tts-ch1", outputs)
        assert not await cache.step_has_valid_cache("job-1", "tts-ch1")

    @pytest.mark.asyncio
    async def test_step_has_valid_cache_missing_file(
        self, cache: StepCache, tmp_path: Path
    ) -> None:
        out = tmp_path / "out.wav"
        outputs = (OutputFile(path=str(out), filename="out.wav", size_bytes=42,
                              checksum="deadbeef", content_type="audio/wav"),)
        await cache.save_outputs("job-1", "tts-ch1", outputs)
        assert not await cache.step_has_valid_cache("job-1", "tts-ch1")
```

(`_checksum` is the existing private helper in `cache.py`; import it for the tests.)

- [ ] **Step 2: Run the new tests to verify they fail (imports not converted yet)**

Run: `uv run pytest tests/shell/test_cache.py::TestStepCache --no-cov -v 2>&1 | tail -15`
Expected: FAIL — `cache.save_outputs` is not a coroutine (it's still sync).

- [ ] **Step 3: Convert `StepCache` methods to async**

In `src/acheron/shell/cache.py`, replace the imports and the `StepCache` class (lines 1-9, 64-112):

```python
"""File-based caching for plans and step outputs."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import aiofiles
from pydantic import TypeAdapter

from acheron.core.errors import CacheCorruptedError, CacheMissError
from acheron.core.models import OutputFile, Plan

_plan_adapter = TypeAdapter(Plan)
_output_adapter = TypeAdapter(tuple[OutputFile, ...])


def _checksum(path: Path) -> str:
    """Compute SHA-256 hex digest of a file. Blocking — wrap in to_thread from async callers."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


class PlanCache:
    """Persists and loads pipeline plans to/from disk."""

    def __init__(self, data_dir: str | Path = "/data/jobs") -> None:
        self._data_dir = Path(data_dir)

    @property
    def data_dir(self) -> Path:
        """The root directory for cached plans and step outputs."""
        return self._data_dir

    def save_plan(self, plan: Plan) -> Path:
        """Save a plan as JSON. Returns the path to the plan file."""
        plan_dir = self._data_dir / plan.plan_id
        plan_dir.mkdir(parents=True, exist_ok=True)
        plan_file = plan_dir / "plan.json"
        plan_file.write_text(_plan_adapter.dump_json(plan, indent=2).decode())
        return plan_file

    def load_plan(self, plan_id: str) -> Plan:
        """Load a plan from disk.

        Raises:
            CacheMissError: If the plan file does not exist.
            CacheCorruptedError: If the plan file is malformed.
        """
        plan_file = self._data_dir / plan_id / "plan.json"
        if not plan_file.exists():
            msg = f"Plan not found: {plan_id}"
            raise CacheMissError(msg)
        try:
            return _plan_adapter.validate_json(plan_file.read_text())
        except Exception as exc:
            msg = f"Corrupted plan file: {plan_id}"
            raise CacheCorruptedError(msg) from exc

    def plan_exists(self, plan_id: str) -> bool:
        """Check whether a plan file exists on disk."""
        return (self._data_dir / plan_id / "plan.json").exists()


class StepCache:
    """Persists and loads step output manifests asynchronously."""

    def __init__(self, data_dir: str | Path = "/data/jobs") -> None:
        self._data_dir = Path(data_dir)

    async def save_outputs(
        self, job_id: str, step_id: str, outputs: tuple[OutputFile, ...]
    ) -> None:
        """Write output manifest. Creates the step directory if needed."""
        step_dir = self._data_dir / job_id / step_id
        step_dir.mkdir(parents=True, exist_ok=True)
        manifest_file = step_dir / "manifest.json"
        manifest = _output_adapter.dump_json(outputs, indent=2)
        async with aiofiles.open(manifest_file, "wb") as f:
            await f.write(manifest)

    async def load_outputs(self, job_id: str, step_id: str) -> tuple[OutputFile, ...]:
        """Load output files from a step manifest.

        Raises:
            CacheMissError: If the manifest does not exist.
            CacheCorruptedError: If the manifest is malformed.
        """
        manifest_file = self._data_dir / job_id / step_id / "manifest.json"
        if not manifest_file.exists():
            msg = f"Step cache miss: {job_id}/{step_id}"
            raise CacheMissError(msg)
        try:
            async with aiofiles.open(manifest_file, "rb") as f:
                blob = await f.read()
        except OSError as exc:
            msg = f"Corrupted manifest: {job_id}/{step_id}"
            raise CacheCorruptedError(msg) from exc
        try:
            return _output_adapter.validate_json(blob)
        except Exception as exc:
            msg = f"Corrupted manifest: {job_id}/{step_id}"
            raise CacheCorruptedError(msg) from exc

    async def step_has_valid_cache(self, job_id: str, step_id: str) -> bool:
        """Check if a step has a valid manifest with all files present and checksums matching."""
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
            checksum = await asyncio.to_thread(_checksum, file_path)
            if checksum != output.checksum:
                return False
        return True
```

(Note: I added `# noqa: SLF001` to the test's `cache._data_dir` access if needed; in the test file the test class can use a local `data_dir = cache._data_dir` instead to avoid the lint.)

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `uv run pytest tests/shell/test_cache.py::TestStepCache --no-cov -v 2>&1 | tail -15`
Expected: PASS.

- [ ] **Step 5: Verify the rest of the suite still works (PlanCache callers may break)**

Run: `just type-check 2>&1 | tail -20`
Expected: PASS or surface any sync callers of `StepCache` (the only current callers are the tests in this file, so it should be clean).

- [ ] **Step 6: Commit**

```bash
git add src/acheron/shell/cache.py tests/shell/test_cache.py
git commit -m "feat(cache): convert StepCache to async via aiofiles"
```

---

## Task 5: Wire `_step_cache` into the orchestrator

**Files:**
- Modify: `src/acheron/shell/orchestrator.py`

- [ ] **Step 1: Construct `_step_cache` in `Orchestrator.__init__`**

In `src/acheron/shell/orchestrator.py`, update the import (line 1-30 area) to bring in `StepCache`:

```python
from acheron.shell.cache import PlanCache, StepCache
```

And in `__init__`, after `self._cache = cache` (line 113), add:

```python
        self._step_cache = StepCache(cache.data_dir)
```

- [ ] **Step 2: Pass `step_cache` to the factory in `_execute`**

In `src/acheron/shell/orchestrator.py`, find the `executor = create_executor(tracked.strategy, self._handler)` line (line 259) and update it:

```python
                executor = create_executor(
                    tracked.strategy,
                    self._handler,
                    step_cache=self._step_cache,
                )
```

- [ ] **Step 3: Run mypy and existing orchestrator tests**

Run: `uv run mypy src/acheron/ 2>&1 | tail -10`
Expected: PASS.

Run: `uv run pytest tests/shell/test_orchestrator.py --no-cov 2>&1 | tail -5`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/acheron/shell/orchestrator.py
git commit -m "feat(orchestrator): construct StepCache and pass to executor factory"
```

---

## Task 6: `StreamingExecutor` skeleton — TDD normal completion

**Files:**
- Create: `src/acheron/shell/executors/streaming.py`
- Create: `tests/shell/test_streaming_executor.py`

- [ ] **Step 1: Write the first failing test (normal completion)**

In `tests/shell/test_streaming_executor.py`, create the file with:

```python
"""Tests for the StreamingExecutor."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
import pytest_asyncio

from acheron.core.models import (
    ExecutorStrategy,
    JobResult,
    JobStatus,
    JobMetrics,
    OutputFile,
    Plan,
    PlanStep,
    StepStatus,
    WorkerType,
)
from acheron.shell.cache import StepCache
from acheron.shell.executors.streaming import StreamingExecutor
from acheron.shell.executors._utils import StepHandler

# A factory for simple, real on-disk output files (so step_has_valid_cache would pass).
def _real_output(tmp_path: Path, name: str, body: bytes = b"x" * 16) -> OutputFile:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(body)
    return OutputFile(
        path=str(p),
        filename=name,
        size_bytes=len(body),
        # The streaming executor's load path does not currently re-checksum.
        # Leave a placeholder checksum; tests can override if needed.
        checksum="placeholder",
        content_type="audio/wav",
    )


def _make_handler(
    results: dict[str, list[OutputFile]],
) -> tuple[StepHandler, list[str]]:
    """Build a handler that returns the named outputs and records calls.

    Returns the handler and a list that gets each step_id appended on call.
    """
    calls: list[str] = []

    async def handler(step: PlanStep, plan: Plan) -> JobResult:
        calls.append(step.step_id)
        # Tiny sleep so we can prove concurrency ordering if we ever need to.
        await asyncio.sleep(0)
        outputs = tuple(results[step.step_id])
        return JobResult(
            job_id=plan.job_id,
            status=JobStatus.SUCCESS,
            outputs=outputs,
            metrics=JobMetrics(duration_seconds=0.0),
        )

    return handler, calls


def _linear_plan(job_id: str = "job-1", plan_id: str = "plan-1") -> Plan:
    """A 3-step plan: extract -> chunk -> package. Linear pipeline."""
    return Plan(
        plan_id=plan_id,
        job_id=job_id,
        source_type="epub",
        source_language="en",
        target_language="es",
        executor_strategy=ExecutorStrategy.STREAMING,
        steps=(
            PlanStep(
                step_id="extract",
                type=WorkerType.EXTRACTION,
                depends_on=(),
                status=StepStatus.PENDING,
                payload={"source_path": "/tmp/x"},
            ),
            PlanStep(
                step_id="chunk",
                type=WorkerType.CHUNKING,
                depends_on=("extract",),
                status=StepStatus.PENDING,
                payload={},
            ),
            PlanStep(
                step_id="package",
                type=WorkerType.PACKAGING,
                depends_on=("chunk",),
                status=StepStatus.PENDING,
                payload={},
            ),
        ),
    )


@pytest_asyncio.fixture
async def step_cache(tmp_path: Path) -> StepCache:
    return StepCache(tmp_path)


class TestNormalCompletion:
    @pytest.mark.asyncio
    async def test_three_step_plan_completes(
        self, tmp_path: Path, step_cache: StepCache
    ) -> None:
        """A linear 3-step plan runs to completion; outputs come from the cache scan."""
        plan = _linear_plan()
        outputs = {
            "extract": [_real_output(tmp_path, "extracted.txt")],
            "chunk": [_real_output(tmp_path, "chunked.txt")],
            "package": [_real_output(tmp_path, "out.wav", body=b"audio-bytes")],
        }
        handler, calls = _make_handler(outputs)
        executor = StreamingExecutor(handler, step_cache)

        result = await executor.run(plan)

        assert result.status == "completed"
        assert result.completed_steps == 3
        assert result.total_steps == 3
        assert calls == ["extract", "chunk", "package"]
        # All three steps wrote manifests; PlanResult.outputs is the cache scan.
        assert len(result.outputs) == 3
        filenames = {o.filename for o in result.outputs}
        assert filenames == {"extracted.txt", "chunked.txt", "out.wav"}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/shell/test_streaming_executor.py::TestNormalCompletion --no-cov -v 2>&1 | tail -10`
Expected: FAIL with `ModuleNotFoundError: No module named 'acheron.shell.executors.streaming'`.

- [ ] **Step 3: Write the minimal `StreamingExecutor` skeleton**

Create `src/acheron/shell/executors/streaming.py`:

```python
"""Streaming pipeline executor — per-stage asyncio.Queue pipeline.

The plan's stages are dispatched sequentially via bounded queues. Each stage
runs in the outer ``asyncio.TaskGroup`` so a single failure cancels the
rest cleanly. Outputs are written to ``StepCache`` after each stage and
``PlanResult.outputs`` is built by scanning the cache at the end.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from acheron.core.errors import AcheronError, PipelineError, WorkerError
from acheron.core.interfaces import Executor
from acheron.core.models import JobResult, Plan, PlanResult
from acheron.shell.executors._utils import StepHandler, topological_order

if TYPE_CHECKING:
    from acheron.shell.cache import StepCache


# Sentinel pushed on downstream queues to signal "no more work".
_END: None = None


class StreamingExecutor(Executor):
    """Pipeline executor with bounded backpressure and TaskGroup cancellation."""

    def __init__(
        self,
        handler: StepHandler,
        step_cache: StepCache,
        *,
        queue_size: int = 4,
        step_timeout: float = 1800.0,
    ) -> None:
        self._handler = handler
        self._cache = step_cache
        self._queue_size = queue_size
        self._step_timeout = step_timeout

    async def run(self, plan: Plan) -> PlanResult:
        """Run the plan as a streaming pipeline. Returns a PlanResult."""
        start = time.monotonic()
        steps = topological_order(plan.steps)
        if not steps:
            return self._empty_result(plan, start)

        # Build N+1 stages: each step has a producer stage that runs the
        # handler, plus the final stage that aggregates the last result.
        queues: list[asyncio.Queue[JobResult | None]] = [
            asyncio.Queue(maxsize=self._queue_size) for _ in range(len(steps) + 1)
        ]
        # Seed the first queue with a sentinel so the first stage knows to start.
        # (We use None as the "go" signal — the first stage produces the first real
        # result; subsequent stages consume real results until they see None.)
        await queues[0].put(_END)

        completed = 0
        total_cost = 0.0
        last_error: AcheronError | None = None

        try:
            async with asyncio.TaskGroup() as tg:
                # Stage coroutine: read from upstream, run step, write to downstream.
                # The first stage reads the seed sentinel and dispatches the first step.
                stage_tasks = []
                for i, step in enumerate(steps):
                    upstream = queues[i]
                    downstream = queues[i + 1]
                    is_last = i == len(steps) - 1
                    stage_tasks.append(
                        tg.create_task(self._stage(step, plan, upstream, downstream, is_last))
                    )
                # We also need to consume the final queue so the last stage's
                # sentinel drains cleanly. The last stage writes its result
                # to queues[-1] and then writes _END; the run loop below
                # reads the first item and exits.
                final_queue = queues[-1]
                first = await final_queue.get()
                if isinstance(first, AcheronError):
                    raise first
                # Drain the rest in a fire-and-forget task to keep the queue empty
                # for downstream consumers (none in this case, but tidy).
                async def _drain() -> None:
                    while True:
                        item = await final_queue.get()
                        if item is _END:
                            return
                tg.create_task(_drain())

                # Wait for all stages to finish.
                for task in stage_tasks:
                    task.result()  # raises if the stage raised
        except BaseExceptionGroup as eg:
            # Look for an AcheronError in the group (any sibling cancellations
            # are BaseException but not AcheronError — those don't count).
            acheron = [e for e in eg.exceptions if isinstance(e, AcheronError)]
            if acheron:
                last_error = acheron[0]
            elif eg.exceptions:
                inner = eg.exceptions[0]
                last_error = PipelineError(f"streaming failure: {inner}") from inner

        # Build the final outputs by scanning the cache.
        outputs: list[OutputFile] = []
        for step in steps:
            try:
                step_outputs = await self._cache.load_outputs(plan.job_id, step.step_id)
            except Exception:  # noqa: BLE001 — CacheMissError etc.
                continue
            outputs.extend(step_outputs)

        if last_error is None:
            return PlanResult(
                plan_id=plan.plan_id,
                status="completed",
                completed_steps=len(steps),
                total_steps=len(steps),
                outputs=tuple(outputs),
                total_cost=total_cost,
                total_duration_seconds=time.monotonic() - start,
                errors=(),
            )

        # All-or-nothing failure path: any stage failure = status "failed".
        return PlanResult(
            plan_id=plan.plan_id,
            status="failed",
            completed_steps=0,
            total_steps=len(steps),
            outputs=tuple(outputs),
            total_cost=total_cost,
            total_duration_seconds=time.monotonic() - start,
            errors=(str(last_error),),
        )

    async def _stage(
        self,
        step: PlanStep,
        plan: Plan,
        upstream: asyncio.Queue[JobResult | None],
        downstream: asyncio.Queue[JobResult | None],
        is_last: bool,
    ) -> None:
        """Stage consumer: read upstream, dispatch, write downstream + cache."""
        # First stage reads the seed sentinel; subsequent stages read real results.
        try:
            _ = await upstream.get()
            # Dispatch the step with timeout.
            try:
                result = await asyncio.wait_for(
                    self._handler(step, plan),
                    timeout=self._step_timeout,
                )
            except asyncio.TimeoutError as exc:
                msg = f"step {step.step_id} timed out after {self._step_timeout}s"
                raise WorkerError(msg) from exc

            # Wrap unexpected exceptions.
            if not isinstance(result, JobResult):
                msg = f"handler returned non-JobResult for step {step.step_id}"
                raise PipelineError(msg)

            # Write outputs to cache.
            await self._cache.save_outputs(plan.job_id, step.step_id, result.outputs)

            # Forward to downstream.
            await downstream.put(result)
        finally:
            # Always send a sentinel on downstream so any consumers can drain.
            await downstream.put(_END)

    def _empty_result(self, plan: Plan, start: float) -> PlanResult:
        return PlanResult(
            plan_id=plan.plan_id,
            status="completed",
            completed_steps=0,
            total_steps=0,
            outputs=(),
            total_cost=0.0,
            total_duration_seconds=time.monotonic() - start,
            errors=(),
        )
```

You'll also need to add the `OutputFile` import to the streaming file. Add it to the existing import block:

```python
from acheron.core.models import JobResult, OutputFile, Plan, PlanResult
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/shell/test_streaming_executor.py::TestNormalCompletion --no-cov -v 2>&1 | tail -20`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/acheron/shell/executors/streaming.py tests/shell/test_streaming_executor.py
git commit -m "feat(executors): add StreamingExecutor with normal-completion path"
```

---

## Task 7: TDD step timeout → `WorkerError`

**Files:**
- Modify: `tests/shell/test_streaming_executor.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/shell/test_streaming_executor.py`:

```python
class TestStepTimeout:
    @pytest.mark.asyncio
    async def test_slow_handler_raises_worker_error(
        self, tmp_path: Path, step_cache: StepCache
    ) -> None:
        """A handler that exceeds the step timeout raises WorkerError."""
        plan = _linear_plan()

        async def slow_handler(step: PlanStep, plan: Plan) -> JobResult:
            await asyncio.sleep(0.5)
            return JobResult(
                job_id=plan.job_id,
                status=JobStatus.SUCCESS,
                outputs=(_real_output(tmp_path, "out.wav"),),
                metrics=JobMetrics(duration_seconds=0.0),
            )

        executor = StreamingExecutor(slow_handler, step_cache, step_timeout=0.05)
        result = await executor.run(plan)

        assert result.status == "failed"
        assert "timed out" in result.errors[0].lower()
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `uv run pytest tests/shell/test_streaming_executor.py::TestStepTimeout --no-cov -v 2>&1 | tail -10`
Expected: PASS (the timeout wrapping is already in the skeleton).

- [ ] **Step 3: Commit (if anything new was needed) or skip if not**

If the test passed without code changes, skip this commit and move on.

---

## Task 8: TDD `WorkerError` from a stage propagates to the result

**Files:**
- Modify: `tests/shell/test_streaming_executor.py`

- [ ] **Step 1: Write the test**

Append to `tests/shell/test_streaming_executor.py`:

```python
class TestWorkerError:
    @pytest.mark.asyncio
    async def test_worker_unavailable_propagates(
        self, tmp_path: Path, step_cache: StepCache
    ) -> None:
        """A WorkerError from a stage surfaces as a failed PlanResult."""
        from acheron.core.errors import WorkerUnavailableError

        plan = _linear_plan()

        async def failing_handler(step: PlanStep, plan: Plan) -> JobResult:
            raise WorkerUnavailableError(f"no worker for {step.step_id}")

        executor = StreamingExecutor(failing_handler, step_cache)
        result = await executor.run(plan)

        assert result.status == "failed"
        assert any("unavailable" in e.lower() for e in result.errors)
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `uv run pytest tests/shell/test_streaming_executor.py::TestWorkerError --no-cov -v 2>&1 | tail -10`
Expected: PASS (the existing `except* AcheronError` clause catches `WorkerUnavailableError`).

- [ ] **Step 3: Skip commit if no changes; otherwise commit**

```bash
git add tests/shell/test_streaming_executor.py
git commit -m "test(streaming): lock in WorkerError propagation"
```

---

## Task 9: TDD unexpected exception → `PipelineError`

**Files:**
- Modify: `tests/shell/test_streaming_executor.py`

- [ ] **Step 1: Write the test**

Append to `tests/shell/test_streaming_executor.py`:

```python
class TestUnexpectedException:
    @pytest.mark.asyncio
    async def test_unhandled_exception_wrapped_as_pipeline_error(
        self, tmp_path: Path, step_cache: StepCache
    ) -> None:
        """A bare RuntimeError is wrapped as PipelineError."""
        plan = _linear_plan()

        async def bad_handler(step: PlanStep, plan: Plan) -> JobResult:
            msg = "boom"
            raise RuntimeError(msg)

        executor = StreamingExecutor(bad_handler, step_cache)
        result = await executor.run(plan)

        assert result.status == "failed"
        assert any("pipeline" in e.lower() for e in result.errors)
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `uv run pytest tests/shell/test_streaming_executor.py::TestUnexpectedException --no-cov -v 2>&1 | tail -10`
Expected: PASS (the `except* BaseException` clause wraps as `PipelineError`).

- [ ] **Step 3: Skip commit if no changes; otherwise commit**

```bash
git add tests/shell/test_streaming_executor.py
git commit -m "test(streaming): lock in PipelineError wrapping for unexpected exceptions"
```

---

## Task 10: TDD cache save failure → `PipelineError`

**Files:**
- Modify: `tests/shell/test_streaming_executor.py`

- [ ] **Step 1: Write the test**

Append to `tests/shell/test_streaming_executor.py`:

```python
class TestCacheFailure:
    @pytest.mark.asyncio
    async def test_save_outputs_failure_wrapped_as_pipeline_error(
        self, tmp_path: Path, step_cache: StepCache
    ) -> None:
        """If save_outputs raises, the failure is wrapped as PipelineError."""
        plan = _linear_plan()

        async def ok_handler(step: PlanStep, plan: Plan) -> JobResult:
            return JobResult(
                job_id=plan.job_id,
                status=JobStatus.SUCCESS,
                outputs=(_real_output(tmp_path, "out.wav"),),
                metrics=JobMetrics(duration_seconds=0.0),
            )

        executor = StreamingExecutor(ok_handler, step_cache)

        # Monkey-patch the cache to raise.
        async def broken_save(*_args, **_kwargs):
            msg = "disk full"
            raise OSError(msg)
        step_cache.save_outputs = broken_save  # type: ignore[assignment]

        result = await executor.run(plan)

        assert result.status == "failed"
        assert any("save_outputs" in e.lower() for e in result.errors)
```

- [ ] **Step 2: Run the test to verify it fails (or passes)**

Run: `uv run pytest tests/shell/test_streaming_executor.py::TestCacheFailure --no-cov -v 2>&1 | tail -10`
Expected: PASS if the `except* BaseException` clause catches `OSError` and wraps it; FAIL if the error message doesn't say "save_outputs" (in which case, update the streaming.py error message in the `try/except OSError` block around the save call).

- [ ] **Step 3: If the test failed, fix the streaming executor**

In `src/acheron/shell/executors/streaming.py`, wrap the `await self._cache.save_outputs(...)` call:

```python
            try:
                await self._cache.save_outputs(plan.job_id, step.step_id, result.outputs)
            except Exception as exc:
                msg = f"save_outputs failed for step {step.step_id}"
                raise PipelineError(msg) from exc
```

- [ ] **Step 4: Re-run the test**

Run: `uv run pytest tests/shell/test_streaming_executor.py::TestCacheFailure --no-cov -v 2>&1 | tail -10`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/shell/test_streaming_executor.py src/acheron/shell/executors/streaming.py
git commit -m "test(streaming): wrap cache save_outputs failure as PipelineError"
```

---

## Task 11: TDD outer TaskGroup cancels siblings

**Files:**
- Modify: `tests/shell/test_streaming_executor.py`

- [ ] **Step 1: Write the test**

Append to `tests/shell/test_streaming_executor.py`:

```python
class TestTaskGroupCancellation:
    @pytest.mark.asyncio
    async def test_middle_failure_cancels_upstream_and_downstream(
        self, tmp_path: Path, step_cache: StepCache
    ) -> None:
        """When the middle stage fails, the upstream and downstream stages are cancelled."""
        from acheron.core.errors import WorkerUnavailableError

        plan = _linear_plan()
        started: list[str] = []

        async def handler(step: PlanStep, plan: Plan) -> JobResult:
            started.append(step.step_id)
            if step.step_id == "chunk":
                raise WorkerUnavailableError("chunk failed")
            # Slow downstream so we can observe cancellation.
            await asyncio.sleep(1.0)
            return JobResult(
                job_id=plan.job_id,
                status=JobStatus.SUCCESS,
                outputs=(_real_output(tmp_path, f"{step.step_id}.out"),),
                metrics=JobMetrics(duration_seconds=0.0),
            )

        executor = StreamingExecutor(handler, step_cache, step_timeout=5.0)
        result = await executor.run(plan)

        assert result.status == "failed"
        # extract started, chunk raised, package was cancelled (not completed).
        assert "extract" in started
        assert "chunk" in started
        # The package step's manifest should NOT exist (it was cancelled before
        # reaching the cache write).
        package_manifest = step_cache._data_dir / plan.job_id / "package" / "manifest.json"
        assert not package_manifest.exists()
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `uv run pytest tests/shell/test_streaming_executor.py::TestTaskGroupCancellation --no-cov -v 2>&1 | tail -10`
Expected: PASS (the TaskGroup's natural cancellation behavior handles this).

- [ ] **Step 3: Commit if test was meaningful, otherwise skip**

```bash
git add tests/shell/test_streaming_executor.py
git commit -m "test(streaming): verify TaskGroup cancels siblings on middle-stage failure"
```

---

## Task 12: TDD sentinel drain on cancellation

**Files:**
- Modify: `tests/shell/test_streaming_executor.py`

- [ ] **Step 1: Write the test**

Append to `tests/shell/test_streaming_executor.py`:

```python
class TestSentinelDrain:
    @pytest.mark.asyncio
    async def test_sentinel_propagates_downstream(
        self, tmp_path: Path, step_cache: StepCache
    ) -> None:
        """When a stage fails, downstream stages see the sentinel and exit."""
        from acheron.core.errors import WorkerUnavailableError

        plan = _linear_plan()
        completed: list[str] = []

        async def handler(step: PlanStep, plan: Plan) -> JobResult:
            if step.step_id == "extract":
                raise WorkerUnavailableError("extract failed")
            completed.append(step.step_id)
            return JobResult(
                job_id=plan.job_id,
                status=JobStatus.SUCCESS,
                outputs=(_real_output(tmp_path, f"{step.step_id}.out"),),
                metrics=JobMetrics(duration_seconds=0.0),
            )

        executor = StreamingExecutor(handler, step_cache)
        result = await executor.run(plan)

        assert result.status == "failed"
        # No downstream stage should have "completed" (they were cancelled,
        # not finished).
        assert completed == []
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `uv run pytest tests/shell/test_streaming_executor.py::TestSentinelDrain --no-cov -v 2>&1 | tail -10`
Expected: PASS.

- [ ] **Step 3: Commit if test was meaningful**

```bash
git add tests/shell/test_streaming_executor.py
git commit -m "test(streaming): verify sentinel drain on cancellation"
```

---

## Task 13: TDD `PlanResult.outputs` sourced from cache

**Files:**
- Modify: `tests/shell/test_streaming_executor.py`

- [ ] **Step 1: Write the test**

Append to `tests/shell/test_streaming_executor.py`:

```python
class TestOutputsFromCache:
    @pytest.mark.asyncio
    async def test_outputs_match_cache_contents(
        self, tmp_path: Path, step_cache: StepCache
    ) -> None:
        """PlanResult.outputs is the union of all step manifests in the cache."""
        plan = _linear_plan()
        outputs = {
            "extract": [_real_output(tmp_path, "extracted.txt", body=b"e" * 8)],
            "chunk": [_real_output(tmp_path, "chunk1.txt"), _real_output(tmp_path, "chunk2.txt")],
            "package": [_real_output(tmp_path, "out.wav", body=b"a" * 1024)],
        }
        handler, _ = _make_handler(outputs)
        executor = StreamingExecutor(handler, step_cache)

        result = await executor.run(plan)

        # 4 outputs total: 1 extract + 2 chunk + 1 package.
        assert len(result.outputs) == 4
        # Verify each is also readable from the cache directly.
        for step in plan.steps:
            cached = await step_cache.load_outputs(plan.job_id, step.step_id)
            assert len(cached) == len(outputs[step.step_id])
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `uv run pytest tests/shell/test_streaming_executor.py::TestOutputsFromCache --no-cov -v 2>&1 | tail -10`
Expected: PASS.

- [ ] **Step 3: Commit if test was meaningful**

```bash
git add tests/shell/test_streaming_executor.py
git commit -m "test(streaming): verify PlanResult.outputs is the cache scan"
```

---

## Task 14: Change default strategy to `streaming`

**Files:**
- Modify: `src/acheron/shell/api/schemas.py`
- Modify: `src/acheron/api_client.py`

- [ ] **Step 1: Update the API schema default**

In `src/acheron/shell/api/schemas.py` (line 15), change:

```python
    executor_strategy: str = "batch_async"
```

to:

```python
    executor_strategy: str = "streaming"
```

- [ ] **Step 2: Update the API client default**

In `src/acheron/api_client.py` (line 27), change:

```python
        executor_strategy: str = "batch_async",
```

to:

```python
        executor_strategy: str = "streaming",
```

- [ ] **Step 3: Run the full shell + integration test suite to confirm no regressions**

Run: `uv run pytest tests/shell/ tests/integration/ --no-cov 2>&1 | tail -10`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/acheron/shell/api/schemas.py src/acheron/api_client.py
git commit -m "feat(api): change default executor strategy to streaming"
```

---

## Task 15: Final validation

**Files:** (none)

- [ ] **Step 1: Run the full validation gate**

Run: `just validate 2>&1 | tail -30`
Expected: all checks pass. Coverage stays at 95%+.

If anything fails, fix and re-run. Do not skip.

- [ ] **Step 2: Confirm git state**

Run: `git log --oneline -20`
Expected: Layer 9a commits stacked on top of 9b-ii, all on master, all atomic.

---

## Spec Coverage Check

| Spec section | Task |
|---|---|
| `core/errors.py` `PipelineError` | Task 1 |
| `core/models.py` `ExecutorStrategy.STREAMING` | Task 2 |
| `shell/cache.py` `StepCache` async via aiofiles | Tasks 3 + 4 |
| `shell/executors/streaming.py` new `StreamingExecutor` | Task 6 (skeleton) + Tasks 7-13 (TDD) |
| `shell/executors/__init__.py` register STREAMING | Task 2 |
| `shell/orchestrator.py` `_step_cache` | Task 5 |
| `shell/api/schemas.py` default → streaming | Task 14 |
| `api_client.py` default → streaming | Task 14 |
| `pyproject.toml` `aiofiles~=24` | Task 3 |
| `tests/core/test_errors.py` `PipelineError` placement | Task 1 |
| `tests/shell/test_cache.py` async | Task 4 |
| `tests/shell/test_streaming_executor.py` new | Tasks 6-13 |
| Sentinel protocol | Task 6 (skeleton) + Task 12 (test) |
| Per-step timeout | Task 6 (skeleton) + Task 7 (test) |
| TaskGroup cancellation | Task 6 (skeleton) + Task 11 (test) |
| `PlanResult.outputs` from cache scan | Task 6 (skeleton) + Task 13 (test) |
| `WorkerError` propagation | Task 6 (skeleton) + Task 8 (test) |
| `PipelineError` for unexpected exceptions | Task 6 (skeleton) + Task 9 (test) |
| Cache save failure → `PipelineError` | Task 10 |
| `just validate` final gate | Task 15 |
