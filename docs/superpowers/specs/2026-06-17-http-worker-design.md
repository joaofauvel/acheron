# HttpWorker Transport — Design Spec

**Sub-project 1 of Layer 5: GPU Workers + Dashboard**

## Overview

Implement the `HttpWorker` transport for remote GPU workers (RunPod, HuggingFace Inference Endpoints), a health monitoring background task, and a real step handler that dispatches work to registered workers.

## Components

### HttpWorker

**File:** `src/acheron/shell/transports/http.py`

Implements `Worker` and `StreamingWorker` ABCs. Wraps a remote HTTP endpoint.

```python
class HttpWorker(StreamingWorker):
    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None) -> None: ...
```

**Methods:**

- `execute(job)` → `POST {base_url}/execute` with job JSON body, returns `JobResult`
- `capabilities()` → `GET {base_url}/capabilities`, returns `WorkerCapabilities`
- `health()` → `GET {base_url}/health`, returns `True` on 200, `False` otherwise
- `submit_batch(batch)` → `POST {base_url}/submit-batch` with batch JSON, returns handle string
- `poll_batch(handle)` → `GET {base_url}/poll/{handle}`, returns `BatchStatus`
- `collect_results(handle)` → pulls results from completed batch

**Error handling:** Network errors (timeout, connection refused) raise `WorkerUnavailableError`. HTTP 4xx/5xx raise `WorkerError` with status and detail.

**Batch polling:** 5s default interval, configurable via constructor parameter.

### HealthMonitor

**File:** `src/acheron/shell/health.py`

Asyncio background task that periodically checks worker health.

```python
class HealthMonitor:
    def __init__(self, registry: WorkerRegistry, interval: float = 30.0) -> None: ...
    async def start(self) -> None: ...   # creates asyncio task
    async def stop(self) -> None: ...    # cancels task
```

**Behavior:**
- Every `interval` seconds, iterate all registered workers
- Call `GET {endpoint}/health` via httpx
- On success: `registry.record_health_success(worker_id)`
- On failure: `registry.record_health_failure(worker_id)` — returns `True` if worker was removed (3 consecutive failures)
- Logs all state changes

**Lifecycle:** Starts when orchestrator is created. Stops when orchestrator is shut down. The orchestrator's `__init__` creates and starts the monitor; a `shutdown()` method stops it.

### StepHandler

**File:** `src/acheron/shell/step_handler.py`

Factory that creates a `StepHandler` callable dispatching to registered workers.

```python
def create_step_handler(registry: WorkerRegistry) -> StepHandler: ...
```

**Behavior:**
- Returns `async def handler(step: PlanStep, plan: Plan) -> JobResult`
- Looks up a worker matching `step.type` and the plan's source/target languages
- Constructs a `Job` from the step payload, step type, and plan context (job_id, chapter_id)
- For non-batch steps: calls `worker.execute(job)`, returns `JobResult`
- For batch steps: if worker is a `StreamingWorker`, calls `submit_batch()` then `poll_batch()` until complete, then `collect_results()`. Otherwise falls back to `execute()`.
- Raises `WorkerError` if no suitable worker found

### Orchestrator Changes

**File:** `src/acheron/shell/orchestrator.py`

- Add `HealthMonitor` to constructor, start it on creation
- Add `shutdown()` method to stop the monitor
- Wire `create_step_handler(registry)` as the default handler (replacing `_noop_handler`)
- Wire step handler in `create_app()` so the API uses real execution

## Testing

**Unit tests:**
- `tests/shell/test_http_worker.py` — mock httpx responses, test all methods
- `tests/shell/test_health_monitor.py` — mock registry, test health check loop
- `tests/shell/test_step_handler.py` — mock workers, test dispatch logic

**Integration tests:**
- Update existing integration tests to use real step handler (jobs should transition running → completed instead of running → failed)

## Dependencies

No new external dependencies. Uses existing `httpx` for HTTP calls.
