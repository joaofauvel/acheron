# HttpWorker Transport Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement HttpWorker transport, health monitoring, and real step handler so the orchestrator can dispatch work to remote GPU workers.

**Architecture:** HttpWorker wraps httpx calls to remote REST endpoints. HealthMonitor runs an asyncio background task polling worker health. StepHandler dispatches plan steps to registered workers by type and language.

**Tech Stack:** httpx, asyncio, pytest, respx (HTTP mocking)

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/acheron/shell/transports/http.py` | HttpWorker implementing Worker + StreamingWorker |
| `src/acheron/shell/health.py` | HealthMonitor asyncio background task |
| `src/acheron/shell/step_handler.py` | StepHandler factory dispatching to workers |
| `tests/shell/test_http_worker.py` | HttpWorker unit tests |
| `tests/shell/test_health_monitor.py` | HealthMonitor unit tests |
| `tests/shell/test_step_handler.py` | StepHandler unit tests |

---

### Task 1: HttpWorker — health and capabilities

**Files:**
- Create: `src/acheron/shell/transports/http.py`
- Create: `tests/shell/test_http_worker.py`

- [ ] **Step 1: Write failing tests for health() and capabilities()**

```python
# tests/shell/test_http_worker.py
"""Tests for the HttpWorker transport."""

import httpx
import pytest
import respx

from acheron.core.models import WorkerCapabilities, WorkerType
from acheron.shell.transports.http import HttpWorker

_BASE_URL = "http://worker:8000"


class TestHttpWorkerHealth:
    @respx.mock
    @pytest.mark.asyncio
    async def test_health_returns_true_on_200(self) -> None:
        respx.get(f"{_BASE_URL}/health").mock(return_value=httpx.Response(200))
        worker = HttpWorker(_BASE_URL)
        assert await worker.health() is True

    @respx.mock
    @pytest.mark.asyncio
    async def test_health_returns_false_on_500(self) -> None:
        respx.get(f"{_BASE_URL}/health").mock(return_value=httpx.Response(500))
        worker = HttpWorker(_BASE_URL)
        assert await worker.health() is False

    @respx.mock
    @pytest.mark.asyncio
    async def test_health_returns_false_on_connection_error(self) -> None:
        respx.get(f"{_BASE_URL}/health").mock(side_effect=httpx.ConnectError("refused"))
        worker = HttpWorker(_BASE_URL)
        assert await worker.health() is False


class TestHttpWorkerCapabilities:
    @respx.mock
    @pytest.mark.asyncio
    async def test_capabilities_returns_worker_caps(self) -> None:
        respx.get(f"{_BASE_URL}/capabilities").mock(
            return_value=httpx.Response(
                200,
                json={
                    "worker_type": "tts",
                    "supported_languages_in": ["es", "en"],
                    "supported_languages_out": ["es", "en"],
                    "supported_formats_in": ["text"],
                    "supported_formats_out": ["wav"],
                    "max_payload_bytes": None,
                    "batch_capable": True,
                    "model_source": "huggingface:Qwen/Qwen3-TTS",
                },
            )
        )
        worker = HttpWorker(_BASE_URL)
        caps = await worker.capabilities()
        assert caps.worker_type == WorkerType.TTS
        assert "es" in caps.supported_languages_in
        assert caps.batch_capable is True
        assert caps.model_source == "huggingface:Qwen/Qwen3-TTS"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/shell/test_http_worker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'acheron.shell.transports.http'`

- [ ] **Step 3: Implement HttpWorker with health() and capabilities()**

```python
# src/acheron/shell/transports/http.py
"""HTTP transport for remote workers (RunPod, HuggingFace Inference Endpoints)."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from pydantic import TypeAdapter

from acheron.core.errors import WorkerError, WorkerUnavailableError
from acheron.core.interfaces import StreamingWorker
from acheron.core.models import (
    BatchJob,
    BatchStatus,
    Job,
    JobResult,
    WorkerCapabilities,
    WorkerType,
)

_caps_adapter = TypeAdapter(WorkerCapabilities)
_result_adapter = TypeAdapter(JobResult)
_batch_status_adapter = TypeAdapter(BatchStatus)

logger = logging.getLogger(__name__)


class HttpWorker(StreamingWorker):
    """Worker that delegates execution to a remote HTTP endpoint."""

    def __init__(
        self,
        base_url: str,
        client: httpx.AsyncClient | None = None,
        poll_interval: float = 5.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._poll_interval = poll_interval

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Make an HTTP request, raising WorkerError on failure."""
        url = f"{self._base_url}{path}"
        try:
            if self._client is not None:
                resp = await self._client.request(method, url, **kwargs)
            else:
                async with httpx.AsyncClient() as client:
                    resp = await client.request(method, url, **kwargs)
            resp.raise_for_status()
            return resp
        except httpx.ConnectError as exc:
            msg = f"Worker unreachable: {self._base_url}"
            raise WorkerUnavailableError(msg) from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text
            msg = f"Worker error {exc.response.status_code}: {detail}"
            raise WorkerError(msg) from exc

    async def capabilities(self) -> WorkerCapabilities:
        resp = await self._request("GET", "/capabilities")
        return _caps_adapter.validate_json(resp.content)

    async def execute(self, job: Job) -> JobResult:
        resp = await self._request("POST", "/execute", json=_job_to_dict(job))
        return _result_adapter.validate_json(resp.content)

    async def health(self) -> bool:
        try:
            resp = await self._request("GET", "/health")
            return resp.status_code == httpx.codes.OK
        except (WorkerError, WorkerUnavailableError):
            return False

    async def submit_batch(self, batch: BatchJob) -> str:
        resp = await self._request(
            "POST",
            "/submit-batch",
            json={"batch_id": batch.batch_id, "jobs": [_job_to_dict(j) for j in batch.jobs]},
        )
        return resp.json()["batch_handle"]

    async def poll_batch(self, batch_handle: str) -> BatchStatus:
        resp = await self._request("GET", f"/poll/{batch_handle}")
        return _batch_status_adapter.validate_json(resp.content)

    async def collect_results(self, batch_handle: str) -> tuple[JobResult, ...]:
        resp = await self._request("GET", f"/poll/{batch_handle}")
        status = _batch_status_adapter.validate_json(resp.content)
        return status.results


def _job_to_dict(job: Job) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "job_type": job.job_type.value,
        "payload": job.payload,
        "chapter_id": job.chapter_id,
        "sequence_ids": list(job.sequence_ids) if job.sequence_ids else None,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/shell/test_http_worker.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/acheron/shell/transports/http.py tests/shell/test_http_worker.py
git commit -m "feat(worker): add HttpWorker with health and capabilities"
```

---

### Task 2: HttpWorker — execute

**Files:**
- Modify: `tests/shell/test_http_worker.py`
- Modify: `src/acheron/shell/transports/http.py`

- [ ] **Step 1: Write failing test for execute()**

Append to `tests/shell/test_http_worker.py`:

```python
from acheron.core.models import Job, JobMetrics, JobResult, JobStatus


class TestHttpWorkerExecute:
    @respx.mock
    @pytest.mark.asyncio
    async def test_execute_returns_job_result(self) -> None:
        respx.post(f"{_BASE_URL}/execute").mock(
            return_value=httpx.Response(
                200,
                json={
                    "job_id": "j-1",
                    "status": "success",
                    "outputs": [],
                    "metrics": {"duration_seconds": 1.5},
                    "error": None,
                },
            )
        )
        worker = HttpWorker(_BASE_URL)
        job = Job(job_id="j-1", job_type=WorkerType.TTS, payload={"text": "hola"}, chapter_id="ch1")
        result = await worker.execute(job)
        assert result.status == JobStatus.SUCCESS
        assert result.job_id == "j-1"
        assert result.metrics.duration_seconds == 1.5

    @respx.mock
    @pytest.mark.asyncio
    async def test_execute_raises_on_server_error(self) -> None:
        respx.post(f"{_BASE_URL}/execute").mock(return_value=httpx.Response(500, text="GPU OOM"))
        worker = HttpWorker(_BASE_URL)
        job = Job(job_id="j-1", job_type=WorkerType.TTS, payload={}, chapter_id="ch1")
        with pytest.raises(WorkerError, match="500"):
            await worker.execute(job)

    @respx.mock
    @pytest.mark.asyncio
    async def test_execute_raises_on_connection_error(self) -> None:
        respx.post(f"{_BASE_URL}/execute").mock(side_effect=httpx.ConnectError("refused"))
        worker = HttpWorker(_BASE_URL)
        job = Job(job_id="j-1", job_type=WorkerType.TTS, payload={}, chapter_id="ch1")
        with pytest.raises(WorkerUnavailableError):
            await worker.execute(job)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/shell/test_http_worker.py::TestHttpWorkerExecute -v`
Expected: FAIL (execute not yet implemented)

- [ ] **Step 3: Verify execute() is already implemented**

The `execute()` method was added in Task 1 Step 3. If the tests pass, move to Step 5.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/shell/test_http_worker.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/shell/test_http_worker.py
git commit -m "test(worker): add HttpWorker execute tests"
```

---

### Task 3: HttpWorker — batch operations

**Files:**
- Modify: `tests/shell/test_http_worker.py`

- [ ] **Step 1: Write failing tests for batch operations**

Append to `tests/shell/test_http_worker.py`:

```python
from acheron.core.models import BatchJob, BatchStatus


class TestHttpWorkerBatch:
    @respx.mock
    @pytest.mark.asyncio
    async def test_submit_batch_returns_handle(self) -> None:
        respx.post(f"{_BASE_URL}/submit-batch").mock(
            return_value=httpx.Response(200, json={"batch_handle": "batch-abc"})
        )
        worker = HttpWorker(_BASE_URL)
        batch = BatchJob(
            batch_id="b-1",
            jobs=(
                Job(job_id="j-1", job_type=WorkerType.TTS, payload={}, chapter_id="ch1"),
                Job(job_id="j-2", job_type=WorkerType.TTS, payload={}, chapter_id="ch1"),
            ),
        )
        handle = await worker.submit_batch(batch)
        assert handle == "batch-abc"

    @respx.mock
    @pytest.mark.asyncio
    async def test_poll_batch_returns_status(self) -> None:
        respx.get(f"{_BASE_URL}/poll/batch-abc").mock(
            return_value=httpx.Response(
                200,
                json={
                    "batch_id": "b-1",
                    "total": 2,
                    "completed": 1,
                    "failed": 0,
                    "pending": 1,
                    "results": [],
                },
            )
        )
        worker = HttpWorker(_BASE_URL)
        status = await worker.poll_batch("batch-abc")
        assert status.total == 2
        assert status.completed == 1
        assert status.pending == 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_collect_results_returns_completed(self) -> None:
        respx.get(f"{_BASE_URL}/poll/batch-abc").mock(
            return_value=httpx.Response(
                200,
                json={
                    "batch_id": "b-1",
                    "total": 1,
                    "completed": 1,
                    "failed": 0,
                    "pending": 0,
                    "results": [
                        {
                            "job_id": "j-1",
                            "status": "success",
                            "outputs": [],
                            "metrics": {"duration_seconds": 0.5},
                            "error": None,
                        }
                    ],
                },
            )
        )
        worker = HttpWorker(_BASE_URL)
        results = await worker.collect_results("batch-abc")
        assert len(results) == 1
        assert results[0].status == JobStatus.SUCCESS
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `uv run pytest tests/shell/test_http_worker.py::TestHttpWorkerBatch -v`
Expected: PASS (batch methods already implemented in Task 1)

- [ ] **Step 3: Commit**

```bash
git add tests/shell/test_http_worker.py
git commit -m "test(worker): add HttpWorker batch operation tests"
```

---

### Task 4: HealthMonitor

**Files:**
- Create: `src/acheron/shell/health.py`
- Create: `tests/shell/test_health_monitor.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/shell/test_health_monitor.py
"""Tests for the health monitor."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from acheron.core.models import WorkerCapabilities, WorkerType
from acheron.shell.health import HealthMonitor
from acheron.shell.registry import WorkerRegistry


def _tts_caps() -> WorkerCapabilities:
    return WorkerCapabilities(
        worker_type=WorkerType.TTS,
        supported_languages_in=frozenset({"es"}),
        supported_languages_out=frozenset({"es"}),
        supported_formats_in=frozenset({"text"}),
        supported_formats_out=frozenset({"wav"}),
        max_payload_bytes=None,
        batch_capable=True,
        model_source=None,
    )


class TestHealthMonitor:
    @pytest.mark.asyncio
    async def test_start_and_stop(self) -> None:
        reg = WorkerRegistry()
        monitor = HealthMonitor(reg, interval=0.01)
        await monitor.start()
        assert monitor._task is not None
        await monitor.stop()
        assert monitor._task.done()

    @pytest.mark.asyncio
    async def test_records_success_for_healthy_worker(self) -> None:
        reg = WorkerRegistry()
        reg.register("w1", "http://worker", "http", _tts_caps())
        health_check = AsyncMock(return_value=True)
        monitor = HealthMonitor(reg, interval=0.01, health_check=health_check)
        await monitor.start()
        await asyncio.sleep(0.05)
        await monitor.stop()
        health_check.assert_called()
        w = reg.get("w1")
        assert w is not None
        assert w.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_records_failure_for_unhealthy_worker(self) -> None:
        reg = WorkerRegistry()
        reg.register("w1", "http://worker", "http", _tts_caps())
        health_check = AsyncMock(return_value=False)
        monitor = HealthMonitor(reg, interval=0.01, health_check=health_check)
        await monitor.start()
        await asyncio.sleep(0.05)
        await monitor.stop()
        w = reg.get("w1")
        assert w is None or w.consecutive_failures > 0

    @pytest.mark.asyncio
    async def test_removes_worker_after_max_failures(self) -> None:
        reg = WorkerRegistry()
        reg.register("w1", "http://worker", "http", _tts_caps())
        health_check = AsyncMock(return_value=False)
        monitor = HealthMonitor(reg, interval=0.01, health_check=health_check)
        await monitor.start()
        await asyncio.sleep(0.15)
        await monitor.stop()
        assert reg.get("w1") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/shell/test_health_monitor.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'acheron.shell.health'`

- [ ] **Step 3: Implement HealthMonitor**

```python
# src/acheron/shell/health.py
"""Background health monitoring for registered workers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Awaitable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from acheron.shell.registry import WorkerRegistry

logger = logging.getLogger(__name__)

type HealthCheckFn = Callable[[str], Awaitable[bool]]


async def _default_health_check(endpoint: str) -> bool:
    """Check worker health via HTTP GET /health."""
    import httpx

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{endpoint}/health", timeout=5.0)
            return resp.status_code == 200
    except (httpx.HTTPError, OSError):
        return False


class HealthMonitor:
    """Periodic background task checking worker health."""

    def __init__(
        self,
        registry: WorkerRegistry,
        interval: float = 30.0,
        health_check: HealthCheckFn | None = None,
    ) -> None:
        self._registry = registry
        self._interval = interval
        self._health_check = health_check or _default_health_check
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the health check background task."""
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Stop the health check background task."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        """Run health checks in a loop."""
        while True:
            await asyncio.sleep(self._interval)
            await self._check_all()

    async def _check_all(self) -> None:
        """Check health of all registered workers."""
        for worker in self._registry.list_all():
            healthy = await self._health_check(worker.endpoint)
            if healthy:
                self._registry.record_health_success(worker.worker_id)
            else:
                removed = self._registry.record_health_failure(worker.worker_id)
                if removed:
                    logger.warning("Removed unhealthy worker %s after %d failures", worker.worker_id, 3)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/shell/test_health_monitor.py -v`
Expected: PASS

- [ ] **Step 5: Run full validation**

Run: `just validate`
Expected: All checks pass

- [ ] **Step 6: Commit**

```bash
git add src/acheron/shell/health.py tests/shell/test_health_monitor.py
git commit -m "feat(health): add HealthMonitor background task"
```

---

### Task 5: StepHandler

**Files:**
- Create: `src/acheron/shell/step_handler.py`
- Create: `tests/shell/test_step_handler.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/shell/test_step_handler.py
"""Tests for the step handler."""

from __future__ import annotations

import pytest

from acheron.core.errors import WorkerError
from acheron.core.models import (
    ExecutorStrategy,
    JobMetrics,
    JobResult,
    JobStatus,
    Plan,
    PlanStep,
    StepStatus,
    WorkerCapabilities,
    WorkerType,
)
from acheron.shell.executors._utils import StepHandler
from acheron.shell.registry import WorkerRegistry
from acheron.shell.step_handler import create_step_handler
from acheron.shell.transports.local import LocalWorker


def _echo_job_result(job) -> JobResult:  # type: ignore[no-untyped-def]
    return JobResult(
        job_id=job.job_id,
        status=JobStatus.SUCCESS,
        outputs=(),
        metrics=JobMetrics(duration_seconds=0.1),
    )


def _tts_caps() -> WorkerCapabilities:
    return WorkerCapabilities(
        worker_type=WorkerType.TTS,
        supported_languages_in=frozenset({"es"}),
        supported_languages_out=frozenset({"es"}),
        supported_formats_in=frozenset({"text"}),
        supported_formats_out=frozenset({"wav"}),
        max_payload_bytes=None,
        batch_capable=True,
        model_source=None,
    )


def _make_plan() -> Plan:
    return Plan(
        plan_id="plan-1",
        job_id="job-1",
        source_type="epub",
        source_language="en",
        target_language="es",
        executor_strategy=ExecutorStrategy.BATCH_ASYNC,
        steps=(
            PlanStep(
                step_id="synthesize",
                type=WorkerType.TTS,
                depends_on=(),
                status=StepStatus.PENDING,
                payload={"target_language": "es", "chapter_id": "ch1"},
            ),
        ),
    )


class TestStepHandler:
    @pytest.mark.asyncio
    async def test_dispatches_to_matching_worker(self) -> None:
        reg = WorkerRegistry()
        local_worker = LocalWorker(worker_type=WorkerType.TTS, handler=_echo_job_result, supported_languages_in=frozenset({"es"}), supported_languages_out=frozenset({"es"}))
        reg.register("tts-1", "http://tts", "http", _tts_caps())
        handler = create_step_handler(reg, worker_factory=lambda _reg: local_worker)
        plan = _make_plan()
        step = plan.steps[0]
        result = await handler(step, plan)
        assert result.status == JobStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_raises_when_no_worker_found(self) -> None:
        reg = WorkerRegistry()
        handler = create_step_handler(reg)
        plan = _make_plan()
        step = plan.steps[0]
        with pytest.raises(WorkerError, match="No worker"):
            await handler(step, plan)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/shell/test_step_handler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'acheron.shell.step_handler'`

- [ ] **Step 3: Implement StepHandler**

```python
# src/acheron/shell/step_handler.py
"""Step handler dispatching plan steps to registered workers."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from acheron.core.errors import WorkerError
from acheron.core.interfaces import Worker
from acheron.core.models import Job, WorkerType
from acheron.shell.transports.http import HttpWorker

if TYPE_CHECKING:
    from acheron.core.models import JobResult, Plan, PlanStep
    from acheron.shell.executors._utils import StepHandler
    from acheron.shell.registry import RegisteredWorker, WorkerRegistry

logger = logging.getLogger(__name__)

type WorkerFactory = Callable[[RegisteredWorker], Worker]


def _default_worker_factory(registered: RegisteredWorker) -> Worker:
    """Create an HttpWorker from a registered worker's endpoint."""
    return HttpWorker(registered.endpoint)


def create_step_handler(
    registry: WorkerRegistry,
    worker_factory: WorkerFactory | None = None,
) -> StepHandler:
    """Create a step handler that dispatches to registered workers."""
    factory = worker_factory or _default_worker_factory

    async def handler(step: PlanStep, plan: Plan) -> JobResult:
        src = plan.source_language
        dst = plan.target_language

        workers = registry.list_all()
        match = None
        for w in workers:
            caps = w.capabilities
            if caps.worker_type != step.type:
                continue
            if src not in caps.supported_languages_in and step.type not in (WorkerType.EXTRACTION, WorkerType.CHUNKING, WorkerType.PACKAGING):
                continue
            if dst not in caps.supported_languages_out and step.type not in (WorkerType.EXTRACTION, WorkerType.CHUNKING, WorkerType.PACKAGING):
                continue
            match = w
            break

        if match is None:
            msg = f"No worker for {step.type.value} ({src} → {dst})"
            raise WorkerError(msg)

        job = Job(
            job_id=f"{plan.job_id}-{step.step_id}",
            job_type=step.type,
            payload=step.payload,
            chapter_id=step.payload.get("chapter_id", ""),
        )

        logger.info("Dispatching %s to %s", step.step_id, match.worker_id)
        worker_instance = factory(match)
        return await worker_instance.execute(job)

    return handler
```

The `worker_factory` parameter allows tests to inject `LocalWorker` or mocks instead of `HttpWorker`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/shell/test_step_handler.py -v`
Expected: PASS

- [ ] **Step 5: Run full validation**

Run: `just validate`
Expected: All checks pass

- [ ] **Step 6: Commit**

```bash
git add src/acheron/shell/step_handler.py tests/shell/test_step_handler.py
git commit -m "feat(step-handler): add step handler dispatching to workers"
```

---

### Task 6: Wire orchestrator with real step handler and health monitor

**Files:**
- Modify: `src/acheron/shell/orchestrator.py`
- Modify: `src/acheron/shell/api/app.py`

- [ ] **Step 1: Update orchestrator to use HealthMonitor and real step handler**

In `src/acheron/shell/orchestrator.py`, add imports and update constructor:

```python
from acheron.shell.health import HealthMonitor
from acheron.shell.step_handler import create_step_handler
```

Update `Orchestrator.__init__` to create and start the health monitor, and wire the step handler:

```python
def __init__(
    self,
    registry: WorkerRegistry,
    cache: PlanCache,
    handler: StepHandler | None = None,
) -> None:
    self._registry = registry
    self._cache = cache
    self._handler = handler or create_step_handler(registry)
    self._job_store = JobStore()
    self._tasks: set[asyncio.Task[None]] = set()
    self._health_monitor = HealthMonitor(registry)

async def start(self) -> None:
    """Start background tasks."""
    await self._health_monitor.start()

async def shutdown(self) -> None:
    """Stop background tasks."""
    await self._health_monitor.stop()
```

- [ ] **Step 2: Update create_app to start orchestrator on startup**

In `src/acheron/shell/api/app.py`, add a lifespan handler:

```python
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    orch: Orchestrator = app.state.orchestrator
    await orch.start()
    yield
    await orch.shutdown()
```

Pass `lifespan=lifespan` to `FastAPI(...)`.

- [ ] **Step 3: Remove _noop_handler from app.py**

Remove the `_noop_handler` function since the orchestrator now defaults to a real handler.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/ -v`
Expected: All tests pass (existing integration tests now use real handler)

- [ ] **Step 5: Run full validation**

Run: `just validate`
Expected: All checks pass

- [ ] **Step 6: Commit**

```bash
git add src/acheron/shell/orchestrator.py src/acheron/shell/api/app.py
git commit -m "feat(orchestrator): wire real step handler and health monitor"
```

---

### Task 7: Final validation and cleanup

- [ ] **Step 1: Run full validation**

Run: `just validate`
Expected: All checks pass, all tests pass, coverage ≥ 80%

- [ ] **Step 2: Verify integration tests show real execution**

Run: `uv run pytest tests/integration/ -v`
Expected: Jobs transition through real execution (may still fail if no workers respond, but the handler is real)

- [ ] **Step 3: Commit any remaining fixes**

```bash
git add -A
git commit -m "chore: final cleanup for HttpWorker sub-project"
```
