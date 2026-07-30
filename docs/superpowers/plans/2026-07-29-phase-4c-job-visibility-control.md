# Phase 4C Job Visibility and Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement all 13 Phase 4C stories as one coherent job visibility and control contract, delivered through five independently verified stages.

**Architecture:** Expand the shared domain, persistence, and wire models first, then consume the contract in the API, CLI, and dashboard. Add cancellation and recovery through the orchestrator's existing per-job locks and step cache, and add a bounded in-memory progress broker for follow/watch/tail observation. Each stage ends with focused tests and a commit; the final stage runs all repository and UX-review gates.

**Tech Stack:** Python 3.14, FastAPI, Pydantic v2, httpx, Click, Rich, Redis, Jinja2, pytest, respx, Just.

## Global Constraints

- The project is greenfield; do not add compatibility defaults, migration layers, or legacy command aliases.
- Keep one public `JobResponse` contract across API, client, CLI, and dashboard.
- Persist the same logical `TrackedJob` record in memory and Redis.
- Use timezone-aware UTC datetimes for `created_at` and `last_persisted_at`.
- Keep operator-visible errors sanitized; never expose credentials, internal URLs, tracebacks, or arbitrary filesystem paths.
- Use TDD for every behavior change: failing test, focused implementation, focused verification, then commit.
- Preserve the existing `just validate` and `just ux-validate` gates.
- Update UX story metadata only after the corresponding behavior and `just ux-verify OPS-###` command pass.
- Do not expand `OPS-003`, `OPS-012`, `OPS-022`, or `OPS-028` beyond contracts consumed by Phase 4C.

---

## File Map

### Core contract and persistence

- Modify `src/acheron/core/models.py`: add typed internal step errors and progress data; update `PlanResult` error typing.
- Modify `src/acheron/core/schemas.py`: add `OutputSummary`, `StepError`, `JobProgress`, `JobLogEvent`, `ErrorResponse`, and the complete `JobResponse` shape.
- Modify `src/acheron/core/errors.py`: add typed lifecycle and cache-invalidation errors with remediation data.
- Modify `src/acheron/shell/job_store.py`: add labels, retry linkage, lifecycle timestamps, and progress fields to `TrackedJob`.
- Modify `src/acheron/shell/stores/memory.py`: stamp persistence time and retain the expanded job record.
- Modify `src/acheron/shell/stores/redis.py`: serialize and deserialize every new job field.
- Modify `src/acheron/shell/api/schemas.py`: add strict submit, retry, and resume request models.

### Orchestration and cache

- Modify `src/acheron/shell/orchestrator.py`: map typed step failures, maintain progress, create linked retries, cancel execution tasks, resume with targeted invalidation, and publish progress events.
- Modify `src/acheron/shell/cache.py`: invalidate selected step cache directories and implement the same behavior for `InMemoryStepCache`.
- Create `src/acheron/shell/job_events.py`: bounded per-job progress event broker.
- Modify `src/acheron/shell/executors/async_executor.py`, `src/acheron/shell/executors/sequential.py`, and `src/acheron/shell/executors/streaming.py`: preserve worker identity and step completion context where executor results are created.
- Modify `src/acheron/shell/local_handlers.py` and transport result construction only where required to populate the new internal result fields.

### API, client, and CLI

- Modify `src/acheron/shell/api/routes/jobs.py`: map the expanded response, add label filtering, retry, cancel, and the new resume request.
- Create `src/acheron/shell/api/routes/job_outputs.py`: serve allowlisted job artifacts.
- Modify `src/acheron/shell/api/app.py`: register the output router.
- Modify `src/acheron/api_client.py`: add cancel, retry, new resume arguments, label filtering, and NDJSON log consumption.
- Modify `src/acheron/cli.py`: render the expanded status, add labels, cancel, retry, selective resume, watch, submit-follow, and tail.

### Dashboard

- Modify `dashboard/app.py`: proxy job detail and safe output downloads.
- Modify `dashboard/templates/index.html`: add the detail target and navigation behavior.
- Modify `dashboard/templates/partials/jobs.html`: add label/error columns and clickable rows.
- Create `dashboard/templates/partials/job_detail.html`: render job metadata, outputs, and attributed errors.

### Tests and UX evidence

- Modify `tests/core/test_models.py` and `tests/core/test_schemas.py`: cover internal and public contract changes.
- Modify `tests/core/test_errors.py`: cover structured lifecycle errors and remediation.
- Modify `tests/shell/api/test_schemas.py`: cover strict request validation.
- Modify `tests/shell/stores/test_redis_job_store.py`: cover expanded Redis round trips.
- Modify `tests/shell/test_cache.py`: cover targeted and dependent invalidation.
- Modify `tests/shell/test_orchestrator.py`: cover progress, cancellation, retry, resume, and events.
- Modify executor and integration fixtures that construct `JobResult` or `PlanResult`.
- Modify `tests/shell/api/test_jobs.py`: cover response mapping, labels, retry, cancel, and resume routes.
- Create `tests/shell/api/test_job_outputs.py`: cover safe artifact serving.
- Modify `tests/test_api_client.py`: cover every new client method and streaming response.
- Modify `tests/shell/test_cli.py`: cover status, labels, retry, resume, watch, follow, and tail.
- Modify `dashboard/tests/test_dashboard.py`: cover unchanged existing partials and new table behavior.
- Create `dashboard/tests/test_job_detail.py`: cover detail and output proxy behavior.
- Modify `tests/integration/test_job_lifecycle.py`: cover complete, failed, cancelled, retried, and selectively resumed jobs.
- Modify applicable first-run tests only if the Compose dashboard journey consumes the new behavior.
- Modify `docs/ux_review/ops.md` and `docs/ux_review/summary.md`: record story verification after implementation.

---

## Stage 1 — JobResponse envelope foundation (`OPS-004`)

### Task 1: Define the complete domain and wire contract

**Files:**
- Modify: `src/acheron/core/models.py`
- Modify: `src/acheron/core/schemas.py`
- Modify: `src/acheron/core/errors.py`
- Modify: `src/acheron/shell/api/schemas.py`
- Test: `tests/core/test_models.py`
- Test: `tests/core/test_schemas.py`
- Test: `tests/core/test_errors.py`
- Test: `tests/shell/api/test_schemas.py`

**Interfaces:**
- Consumes: existing `PlanStatus`, `WorkerType`, `ExecutorStrategy`, `OutputFile`, `PlanResult`, and strict request base model.
- Produces: `StepError`, `JobProgress`, `OutputSummary`, `JobLogEvent`, `ErrorResponse`, expanded `JobResponse`, `RetryJobRequest`, `ResumeJobRequest`, and typed `PlanResult.errors`.

- [ ] **Step 1: Write failing domain-model tests**

Add tests proving that a runtime failure retains step and worker attribution and that a plan result accepts typed errors:

```python
from datetime import UTC, datetime

from acheron.core.models import PlanResult, PlanStatus, StepError, WorkerType


def test_plan_result_preserves_typed_step_errors() -> None:
    error = StepError(
        step_id="step-3",
        worker_type=WorkerType.TTS,
        worker_id="tts-1",
        message="malformed audio",
        timestamp=datetime(2026, 7, 29, tzinfo=UTC),
    )

    result = PlanResult(
        plan_id="plan-1",
        status=PlanStatus.FAILED,
        completed_steps=2,
        total_steps=5,
        outputs=(),
        total_cost=0.0,
        total_duration_seconds=4.5,
        errors=(error,),
    )

    assert result.errors[0].worker_id == "tts-1"
    assert result.errors[0].step_id == "step-3"
```

Add schema tests for the complete response and event contracts:

```python
from datetime import UTC, datetime

from acheron.core.schemas import JobResponse, JobLogEvent


def test_job_response_exposes_phase_4c_fields() -> None:
    response = JobResponse.model_validate(
        {
            "job_id": "job-1",
            "status": "failed",
            "plan_id": "plan-1",
            "label": "atlas-ch1",
            "retries_from": None,
            "source_type": "audio",
            "source_language": "en",
            "target_language": "es",
            "asr_model": "whisper-v3",
            "executor_strategy": "streaming",
            "created_at": "2026-07-29T12:00:00Z",
            "last_persisted_at": "2026-07-29T12:00:05Z",
            "progress": {
                "completed_steps": 2,
                "total_steps": 5,
                "current_step_id": "step-3",
                "current_worker_type": "tts",
                "current_worker_id": "tts-1",
                "eta_seconds": None,
            },
            "total_cost": 0.0,
            "total_duration_seconds": 4.5,
            "total_cost_basis": None,
            "outputs": [],
            "errors": [
                {
                    "step_id": "step-3",
                    "worker_type": "tts",
                    "worker_id": "tts-1",
                    "message": "malformed audio",
                    "timestamp": "2026-07-29T12:00:04Z",
                }
            ],
            "warnings": [],
        }
    )

    assert response.progress.current_worker_id == "tts-1"
    assert response.errors[0].message == "malformed audio"
    assert response.created_at.tzinfo is not None


def test_job_log_event_serializes_as_one_json_object() -> None:
    event = JobLogEvent(
        job_id="job-1",
        timestamp=datetime(2026, 7, 29, tzinfo=UTC),
        status="running",
        step_id="step-3",
        worker_type="tts",
        worker_id="tts-1",
        progress={"completed_steps": 2, "total_steps": 5},
        message="step started",
    )

    assert event.model_dump_json().count("\n") == 0
```

Add strict request tests proving labels are accepted, unknown fields are rejected, retry overrides are optional, and resume accepts repeated invalidation values:

```python
from acheron.shell.api.schemas import RetryJobRequest, ResumeJobRequest, SubmitJobRequest


def test_submit_accepts_label() -> None:
    request = SubmitJobRequest(
        source_type="epub",
        source_path="book.epub",
        source_language="en",
        target_language="es",
        label="atlas-ch1",
    )
    assert request.label == "atlas-ch1"


def test_resume_request_accepts_selected_cache_entries() -> None:
    request = ResumeJobRequest(
        invalidate_steps=["step-47", "step-48"],
        invalidate_chapters=[47],
    )
    assert request.invalidate_steps == ["step-47", "step-48"]
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
uv run pytest tests/core/test_models.py tests/core/test_schemas.py tests/core/test_errors.py tests/shell/api/test_schemas.py -q
```

Expected: FAIL because the new models, fields, and request classes do not exist.

- [ ] **Step 3: Implement the minimal contract**

In `src/acheron/core/models.py`, add a frozen internal error value and change `PlanResult`:

```python
@dataclass(frozen=True)
class StepError:
    """Sanitized failure attribution for one execution step."""

    step_id: str | None
    worker_type: WorkerType | None
    worker_id: str | None
    message: str
    timestamp: datetime

@dataclass(frozen=True)
class PlanResult:
    # existing fields remain unchanged
    errors: tuple[StepError, ...] = ()
```

In `src/acheron/core/schemas.py`, define the public Pydantic models with `datetime` fields, nullable worker identity, and nested progress:

```python
class OutputSummary(BaseModel):
    download_url: str
    filename: str
    size_bytes: int
    content_type: str

class StepError(BaseModel):
    step_id: str | None
    worker_type: WorkerType | None
    worker_id: str | None
    message: str
    timestamp: datetime

class JobProgress(BaseModel):
    completed_steps: int = 0
    total_steps: int = 0
    current_step_id: str | None = None
    current_worker_type: WorkerType | None = None
    current_worker_id: str | None = None
    eta_seconds: float | None = None

class JobLogEvent(BaseModel):
    job_id: str
    timestamp: datetime
    status: PlanStatus
    step_id: str | None = None
    worker_type: WorkerType | None = None
    worker_id: str | None = None
    progress: JobProgress
    message: str

class ErrorResponse(BaseModel):
    type: str
    message: str
    remediation: str | None = None

class JobResponse(BaseModel):
    job_id: str
    status: PlanStatus
    plan_id: str | None
    label: str | None
    retries_from: str | None
    source_type: str
    source_language: str
    target_language: str
    asr_model: str | None
    executor_strategy: ExecutorStrategy
    created_at: datetime
    last_persisted_at: datetime
    progress: JobProgress
    total_cost: float
    total_duration_seconds: float
    total_cost_basis: CostBasis | None
    outputs: list[OutputSummary]
    errors: list[StepError]
    warnings: list[str]
```

In `src/acheron/shell/api/schemas.py`, update `SubmitJobRequest` and add the strict retry/resume bodies:

```python
class SubmitJobRequest(_StrictRequest):
    source_type: str
    source_path: str
    source_language: str
    target_language: str
    executor_strategy: str = "streaming"
    asr_model: str | None = None
    label: str | None = None

class RetryJobRequest(_StrictRequest):
    source_path: str | None = None
    source_language: str | None = None
    target_language: str | None = None
    executor_strategy: str | None = None
    asr_model: str | None = None
    label: str | None = None

class ResumeJobRequest(_StrictRequest):
    invalidate_steps: list[str] = Field(default_factory=list)
    invalidate_chapters: list[int] = Field(default_factory=list)
```

Make `JobResponse.progress` required, replace flat string errors with `list[StepError]`, add the metadata/output fields from the approved spec, and remove the old `force_fresh` request field. Export the new public models and request models from their module `__all__` lists.

Add `NoPlanToResumeError`, `JobNotCancellableError`, and `InvalidationTargetError` under `JobError`; each accepts a message and optional remediation. Give `AcheronError` an explicit constructor:

```python
class AcheronError(Exception):
    def __init__(self, message: str, *, remediation: str | None = None) -> None:
        super().__init__(message)
        self.remediation = remediation
```

Routes can then construct `ErrorResponse(type=type(exc).__name__, message=str(exc), remediation=exc.remediation)` without a compatibility parser.

- [ ] **Step 4: Run the focused tests and verify they pass**

Run:

```bash
uv run pytest tests/core/test_models.py tests/core/test_schemas.py tests/core/test_errors.py tests/shell/api/test_schemas.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit the contract**

```bash
git add src/acheron/core/models.py src/acheron/core/schemas.py src/acheron/core/errors.py \
  src/acheron/shell/api/schemas.py tests/core/test_models.py tests/core/test_schemas.py \
  tests/core/test_errors.py tests/shell/api/test_schemas.py
git commit -m "feat(OPS-004): define Phase 4C job contract"
```

### Task 2: Persist and map the expanded job record

**Files:**
- Modify: `src/acheron/shell/job_store.py`
- Modify: `src/acheron/shell/stores/memory.py`
- Modify: `src/acheron/shell/stores/redis.py`
- Modify: `src/acheron/shell/api/routes/jobs.py`
- Test: `tests/shell/stores/test_redis_job_store.py`
- Test: `tests/shell/api/test_jobs.py`
- Test: `tests/integration/test_job_lifecycle.py`

**Interfaces:**
- Consumes: the Task 1 domain and public models, existing `TrackedJob`, `_serialize_job()`, `_deserialize_job()`, and `_tracked_to_response()`.
- Produces: persisted `label`, `retries_from`, UTC lifecycle timestamps, typed results, nested progress, outputs, and metadata in every job response.

- [ ] **Step 1: Write failing persistence and response-mapping tests**

Extend the existing Redis round-trip fixture with a job containing every new field:

```python
async def test_job_round_trip_preserves_phase_4c_fields(redis_job_store) -> None:
    created = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
    tracked = TrackedJob(
        job_id="job-1",
        request=AudioRequest("/data/book.wav", "en", "es", "whisper-v3"),
        strategy=ExecutorStrategy.STREAMING,
        label="atlas-ch1",
        retries_from="job-old",
        created_at=created,
        last_persisted_at=created,
        progress=JobProgressState(
            completed_steps=2,
            total_steps=5,
            current_step_id="step-3",
            current_worker_type=WorkerType.TTS,
            current_worker_id="tts-1",
        ),
        result=PlanResult(
            plan_id="plan-1",
            status=PlanStatus.FAILED,
            completed_steps=2,
            total_steps=5,
            outputs=(),
            total_cost=0.0,
            total_duration_seconds=4.5,
            errors=(
                StepError(
                    step_id="step-3",
                    worker_type=WorkerType.TTS,
                    worker_id="tts-1",
                    message="malformed audio",
                    timestamp=created,
                ),
            ),
        ),
    )

    await redis_job_store.put(tracked)
    loaded = await redis_job_store.get("job-1")

    assert loaded is not None
    assert loaded.label == "atlas-ch1"
    assert loaded.retries_from == "job-old"
    assert loaded.created_at == created
    assert loaded.result.errors[0].worker_id == "tts-1"
```

Add a route assertion that `GET /jobs/job-1` returns `progress`, output summaries, timestamps, and typed errors rather than the old flat fields.

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
uv run pytest tests/shell/stores/test_redis_job_store.py tests/shell/api/test_jobs.py::TestJobRoutes tests/integration/test_job_lifecycle.py -q
```

Expected: FAIL because `TrackedJob` and the Redis JSON shape do not contain the new fields.

- [ ] **Step 3: Add lifecycle fields and persistence stamping**

In `src/acheron/shell/job_store.py`, add a small internal progress value and lifecycle fields:

```python
@dataclass
class JobProgressState:
    completed_steps: int = 0
    total_steps: int = 0
    current_step_id: str | None = None
    current_worker_type: WorkerType | None = None
    current_worker_id: str | None = None
    eta_seconds: float | None = None

@dataclass
class TrackedJob:
    job_id: str
    request: JobRequest
    strategy: ExecutorStrategy
    label: str | None = None
    retries_from: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_persisted_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    progress: JobProgressState = field(default_factory=JobProgressState)
    plan: Plan | None = None
    result: PlanResult | None = None
    status: PlanStatus = PlanStatus.PENDING
```

In both `InMemoryJobStore.put()` and `RedisJobStore.put()`, set `job.last_persisted_at = datetime.now(UTC)` immediately before storing. Add the new fields to `_serialize_job()` and restore them in `_deserialize_job()` using the existing `TypeAdapter` patterns for domain values and ISO timestamps for datetimes.

- [ ] **Step 4: Map the record to the public response**

Update `_tracked_to_response()` in `src/acheron/shell/api/routes/jobs.py` to build `JobProgress`, `OutputSummary`, and public `StepError` values. Enumerate persisted outputs to produce `download_url` values of `/jobs/{job_id}/outputs/{index}`; use `filename`, `size_bytes`, and `content_type` while keeping `OutputFile.path` internal and not exposing checksum or internal metadata.

The conversion must map the original request by structural match:

```python
match tracked.request:
    case AudioRequest(asr_model=asr_model):
        source_type = "audio"
    case EpubRequest():
        asr_model = None
        source_type = "epub"
```

Set `created_at` and `last_persisted_at` directly from `TrackedJob`, and preserve any route warnings in `warnings`.

- [ ] **Step 5: Run the focused tests and verify they pass**

Run:

```bash
uv run pytest tests/shell/stores/test_redis_job_store.py tests/shell/api/test_jobs.py::TestJobRoutes tests/integration/test_job_lifecycle.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit persistence and mapping**

```bash
git add src/acheron/shell/job_store.py src/acheron/shell/stores/memory.py \
  src/acheron/shell/stores/redis.py src/acheron/shell/api/routes/jobs.py \
  tests/shell/stores/test_redis_job_store.py tests/shell/api/test_jobs.py \
  tests/integration/test_job_lifecycle.py
git commit -m "feat(OPS-004): persist and expose job metadata"
```

### Task 3: Preserve typed step attribution and current progress during execution

**Files:**
- Modify: `src/acheron/shell/orchestrator.py`
- Modify: `src/acheron/shell/executors/async_executor.py`
- Modify: `src/acheron/shell/executors/sequential.py`
- Modify: `src/acheron/shell/executors/streaming.py`
- Modify: `src/acheron/shell/local_handlers.py` only where result context is created
- Modify: `tests/shell/test_orchestrator.py`
- Modify: `tests/shell/test_executors.py`
- Modify: `tests/shell/test_streaming_executor.py`
- Modify: integration fixtures constructing `JobResult` or `PlanResult`

**Interfaces:**
- Consumes: `TrackedJob.progress`, typed `PlanResult.errors`, existing executor callbacks, `PlanStep`, and sanitized exception messages.
- Produces: `Orchestrator._record_step_progress()` that records `StepError` with step/worker identity and updates the current progress snapshot before persistence.

- [ ] **Step 1: Write failing attribution and progress tests**

Add an orchestrator test using a handler that returns a failed result for a known plan step:

```python
async def test_failed_step_records_worker_attribution(orchestrator, failing_handler) -> None:
    tracked = await orchestrator.submit_job(epub_request, ExecutorStrategy.STREAMING)
    await wait_for_terminal(orchestrator, tracked.job_id)

    failed = await orchestrator.get_job(tracked.job_id)
    assert failed is not None
    error = failed.result.errors[0]
    assert error.step_id == "step-2"
    assert error.worker_type == WorkerType.CHUNKING
    assert error.worker_id == "chunking-local"
    assert error.message == "input too long"
    assert failed.progress.completed_steps == 1
```

Add a success test proving the current step is set while a handler is blocked and cleared or finalized when the job reaches a terminal state.

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
uv run pytest tests/shell/test_orchestrator.py tests/shell/test_executors.py tests/shell/test_streaming_executor.py -q
```

Expected: FAIL because errors remain strings and execution does not populate the new progress state.

- [ ] **Step 3: Attach execution context at the orchestration boundary**

Add `worker_id: str | None = None` to the internal `JobResult` dataclass. Keep worker selection in the existing handler/executor path, and set this field when a dispatch selected a worker. Pass the plan step into `_record_step_progress()` and create a domain `StepError` there:

```python
error = StepError(
    step_id=step.step_id,
    worker_type=step.type,
    worker_id=result.worker_id,
    message=result.error,
    timestamp=datetime.now(UTC),
)
```

Update `TrackedJob.progress` before each handler call and after each result. Compute ETA from completed successful step durations and remaining plan steps. Persist via the existing shielded path after every progress update that can be observed by a client.

Update all executor result constructors and test fixtures to provide `worker_id` when known and `None` for local/unit results without dispatch identity.

- [ ] **Step 4: Run the focused tests and verify they pass**

Run:

```bash
uv run pytest tests/shell/test_orchestrator.py tests/shell/test_executors.py tests/shell/test_streaming_executor.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit typed progress**

```bash
git add src/acheron/shell/orchestrator.py src/acheron/shell/executors \
  src/acheron/shell/local_handlers.py tests/shell/test_orchestrator.py \
  tests/shell/test_executors.py tests/shell/test_streaming_executor.py tests/integration
 git commit -m "feat(OPS-013): preserve step failure attribution"
```

### Stage 1 checkpoint

- [ ] Run the contract and lifecycle tests together:

```bash
uv run pytest tests/core tests/shell/stores/test_redis_job_store.py \
  tests/shell/test_orchestrator.py tests/shell/api/test_jobs.py -q
```

- [ ] Confirm `JobResponse` contains metadata, nested progress, outputs, timestamps, and typed errors for both memory and Redis-backed jobs.
- [ ] Confirm no implementation or test still expects `errors: list[str]` or `resume(force_fresh=True)`. 

---

## Stage 2 — Job detail and failure attribution (`OPS-001`, `OPS-010`, `OPS-013`, `OPS-017`, `OPS-023`)

### Task 4: Add API routes and client methods for labels, retry metadata, and output artifacts

**Files:**
- Modify: `src/acheron/shell/api/routes/jobs.py`
- Create: `src/acheron/shell/api/routes/job_outputs.py`
- Modify: `src/acheron/shell/api/app.py`
- Modify: `src/acheron/api_client.py`
- Modify: `tests/shell/api/test_jobs.py`
- Create: `tests/shell/api/test_job_outputs.py`
- Modify: `tests/test_api_client.py`

**Interfaces:**
- Consumes: `_tracked_to_response()`, `SubmitJobRequest`, `RetryJobRequest`, `JobResponse`, `OutputSummary`, and the configured orchestrator data directory.
- Produces: `GET /jobs?label={glob}`, `GET /jobs/{job_id}/outputs/{output_index}`, `AcheronClient.list_jobs(label: str | None)`, and canonical orchestrator download URLs for the dashboard.

- [ ] **Step 1: Write failing route/client tests**

Add a label filter test using `fnmatch.fnmatchcase` semantics:

```python
async def test_list_jobs_filters_by_label(client) -> None:
    response = await client.get("/jobs", params={"label": "atlas-*"})
    assert response.status_code == 200
    assert [job["label"] for job in response.json()["jobs"]] == ["atlas-ch1"]
```

Add output security tests:

```python
async def test_output_route_serves_listed_artifact(client, job_with_output) -> None:
    response = await client.get("/jobs/job-1/outputs/0")
    assert response.status_code == 200
    assert response.content == b"audio"


async def test_output_route_rejects_out_of_range_index(client, job_with_output) -> None:
    response = await client.get("/jobs/job-1/outputs/99")
    assert response.status_code in {400, 404}
```

Add `AcheronClient.list_jobs(label="atlas-*")` request assertions and verify the returned list validates each item as `JobResponse`.

- [ ] **Step 2: Run the focused tests and verify they fail**

```bash
uv run pytest tests/shell/api/test_jobs.py tests/shell/api/test_job_outputs.py tests/test_api_client.py -q
```

Expected: FAIL because label query handling, the output router, and the client parameter do not exist.

- [ ] **Step 3: Implement the route and client changes**

In `list_jobs()`, accept `label: str | None = Query(default=None)`, list all tracked jobs, and filter with `fnmatch.fnmatchcase(job.label or "", label)` when a filter is present. Do not add the time-window or status filters from `OPS-012`.

Create `job_outputs.py` with an allowlisted route:

```python
@router.get("/{job_id}/outputs/{output_index:int}")
async def get_job_output(job_id: str, output_index: int, orch: OrchestratorDep) -> FileResponse:
    tracked = await orch.get_job(job_id)
    if tracked is None:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                type="JobNotFoundError",
                message=f"Job not found: {job_id}",
                remediation="acheron jobs",
            ).model_dump(),
        )
    try:
        output = tracked.result.outputs[output_index] if tracked.result is not None else None
    except IndexError:
        output = None
    if output is None:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                type="OutputNotFoundError",
                message=f"Output not found: {output_index}",
                remediation=f"acheron job status {job_id}",
            ).model_dump(),
        )
    file_fd, stat_result = _open_output_fd(orch.settings.orchestrator.data_dir, job_id, output.path)
    return _PinnedFileResponse(
        file_fd,
        stat_result=stat_result,
        media_type=output.content_type,
        filename=output.filename,
    )
```

`_open_output_fd()` normalizes absolute or relative persisted paths beneath the canonical data root, opens every directory and file component with `O_NOFOLLOW`, uses `O_NONBLOCK` for the final open, rejects non-regular files, and keeps the descriptor pinned while the response is served. Register the router under `/jobs` after the existing jobs router without duplicating the `GET /jobs/{job_id}` route.

Update `AcheronClient.list_jobs()` to:

```python
async def list_jobs(self, *, label: str | None = None) -> list[JobResponse]:
    params = {"label": label} if label is not None else None
    async with httpx.AsyncClient(
        base_url=self._base_url,
        verify=self._ssl_verify,
    ) as client:
        response = await client.get("/jobs", params=params)
        response.raise_for_status()
        return [JobResponse.model_validate(item) for item in response.json()["jobs"]]
```

- [ ] **Step 4: Run the focused tests and verify they pass**

```bash
uv run pytest tests/shell/api/test_jobs.py tests/shell/api/test_job_outputs.py tests/test_api_client.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit API/client detail support**

```bash
git add src/acheron/shell/api/routes/jobs.py src/acheron/shell/api/routes/job_outputs.py \
  src/acheron/shell/api/app.py src/acheron/api_client.py \
  tests/shell/api/test_jobs.py tests/shell/api/test_job_outputs.py tests/test_api_client.py
git commit -m "feat(OPS-010): expose job outputs and labels"
```

### Task 5: Render job detail in the CLI and dashboard

**Files:**
- Modify: `src/acheron/cli.py`
- Modify: `dashboard/app.py`
- Modify: `dashboard/templates/index.html`
- Modify: `dashboard/templates/partials/jobs.html`
- Create: `dashboard/templates/partials/job_detail.html`
- Modify: `tests/shell/test_cli.py`
- Modify: `dashboard/tests/test_dashboard.py`
- Create: `dashboard/tests/test_job_detail.py`

**Interfaces:**
- Consumes: complete `JobResponse`, `AcheronClient.get_job()`, `AcheronClient.list_jobs(label: str | None)`, the output route, and the existing HTMX polling target.
- Produces: status output with outputs/errors, `acheron jobs --label`, clickable dashboard rows, and `/partials/jobs/{job_id}` detail rendering.

- [ ] **Step 1: Write failing CLI and dashboard tests**

Add CLI assertions:

```python
def test_job_status_renders_output_and_step_error(runner, client) -> None:
    result = runner.invoke(main, ["job", "status", "job-1", "--verbose"])
    assert result.exit_code == 0
    assert "Download URL: /jobs/job-1/outputs/0" in result.output
    assert "step=step-3" in result.output
    assert "worker_id=tts-1" in result.output


def test_jobs_accepts_label_filter(runner, client) -> None:
    result = runner.invoke(main, ["jobs", "--label", "atlas-*"])
    assert result.exit_code == 0
    assert "atlas-ch1" in result.output
```

Add dashboard tests proving a failed row contains a link and the detail fragment renders timestamps, output links, and all error attribution fields:

```python
async def test_jobs_partial_links_failed_job(client) -> None:
    response = await client.get("/partials/jobs")
    assert response.status_code == 200
    assert 'href="/partials/jobs/job-1"' in response.text
    assert "last error" in response.text.lower()


async def test_job_detail_renders_outputs_and_step_error(client) -> None:
    response = await client.get("/partials/jobs/job-1")
    assert response.status_code == 200
    assert "tts-1" in response.text
    assert "step-3" in response.text
    assert "http://orchestrator:8000/jobs/job-1/outputs/0" in response.text
```

- [ ] **Step 2: Run the focused tests and verify they fail**

```bash
uv run pytest tests/shell/test_cli.py dashboard/tests/test_dashboard.py dashboard/tests/test_job_detail.py -q
```

Expected: FAIL because the new CLI renderers, dashboard route, and template do not exist.

- [ ] **Step 3: Implement CLI rendering**

Replace the old flat status rendering with explicit sections:

```python
console.print(f"Job: {job.job_id}")
console.print(f"Status: {job.status.value}")
console.print(f"Label: {job.label or '-'}")
console.print(f"Created: {job.created_at.isoformat()}")
console.print(
    f"Progress: {job.progress.completed_steps}/{job.progress.total_steps}"
)
for output in job.outputs:
    console.print(
        f"Download URL: {output.download_url} ({output.size_bytes} bytes, {output.content_type})"
    )
if verbose:
    for error in job.errors:
        console.print(
            f"Error [step={error.step_id}, worker_type={error.worker_type}, "
            f"worker_id={error.worker_id}]: {error.message}"
        )
```

Add `--label` to `list_jobs()` and pass it to the client. Keep existing `--active` and `--completed` behavior unchanged unless the expanded response requires field-access updates.

- [ ] **Step 4: Implement dashboard detail and navigation**

In `dashboard/app.py`, add:

```python
@app.get("/partials/jobs/{job_id}", response_class=HTMLResponse)
async def job_detail_partial(request: Request, job_id: str) -> HTMLResponse:
    data = await _fetch_orchestrator(orchestrator_url, f"/jobs/{job_id}")
    return _TEMPLATES.TemplateResponse(
        request,
        "partials/job_detail.html",
        context={"job": data, "orchestrator_url": orchestrator_url},
    )
```

Dashboard output links use `orchestrator_url + output.download_url` to fetch the canonical unauthenticated orchestrator route directly; no dashboard output proxy route is added.

Update `index.html` with a `#job-detail` target. Update `partials/jobs.html` so each job ID is an HTMX link targeting that element and add label/last-error columns. Create `partials/job_detail.html` with metadata, output links, and an error table containing `step_id`, `worker_type`, `worker_id`, `message`, and `timestamp`.

- [ ] **Step 5: Run the focused tests and verify they pass**

```bash
uv run pytest tests/shell/test_cli.py dashboard/tests/test_dashboard.py dashboard/tests/test_job_detail.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit the detail surfaces**

```bash
git add src/acheron/cli.py dashboard/app.py dashboard/templates \
  tests/shell/test_cli.py dashboard/tests/test_dashboard.py dashboard/tests/test_job_detail.py
git commit -m "feat(OPS-001): add job detail surfaces"
```

### Stage 2 checkpoint

- [ ] Run:

```bash
uv run pytest tests/core tests/shell/api tests/shell/test_cli.py \
  dashboard/tests tests/integration/test_job_lifecycle.py -q
```

- [ ] Verify the dashboard failed-job journey: jobs table → failed row → detail fragment → timestamp, attributed error, and output link.
- [ ] Verify `acheron job status ID --verbose` prints the same attribution represented by the API.
- [ ] Verify `acheron jobs --label 'atlas-*'` filters only labels and does not add `OPS-012` time/status/archive behavior.

---

## Stage 3 — Cancellation foundation (`OPS-008`)

### Task 6: Add orchestrator cancellation with partial persistence

**Files:**
- Modify: `src/acheron/shell/orchestrator.py`
- Modify: `src/acheron/core/errors.py`
- Modify: `tests/shell/test_orchestrator.py`
- Modify: `tests/integration/test_job_lifecycle.py`

**Interfaces:**
- Consumes: `_active_jobs`, `_tasks`, `_job_locks`, `_persist_shielded()`, typed `StepError`, and `TrackedJob.result`.
- Produces: `async def Orchestrator.cancel_job(job_id: str) -> TrackedJob` with serialized cancellation and a persisted `FAILED` result.

- [ ] **Step 1: Write failing cancellation race tests**

Use an `asyncio.Event`-controlled handler so the test can cancel while a step is active:

```python
@pytest.mark.asyncio
async def test_cancel_job_persists_partial_result(orchestrator, blocking_handler) -> None:
    tracked = await orchestrator.submit_job(epub_request, ExecutorStrategy.STREAMING)
    await blocking_handler.started.wait()

    cancelled = await orchestrator.cancel_job(tracked.job_id)

    assert cancelled.status is PlanStatus.FAILED
    assert cancelled.result is not None
    assert cancelled.result.errors[0].message == "cancelled by operator"
    assert cancelled.result.completed_steps < cancelled.result.total_steps
    persisted = await orchestrator.get_job(tracked.job_id)
    assert persisted is not None
    assert persisted.status is PlanStatus.FAILED
```

Add tests that cancelling a missing job raises `JobNotFoundError`, cancelling a completed job raises `JobNotCancellableError`, and a delayed background persist cannot overwrite the cancelled record.

- [ ] **Step 2: Run the focused tests and verify they fail**

```bash
uv run pytest tests/shell/test_orchestrator.py -k 'cancel' tests/integration/test_job_lifecycle.py -q
```

Expected: FAIL because no public cancellation method or operator cancellation state exists.

- [ ] **Step 3: Track execution tasks by job and implement cancellation**

Add `_execution_tasks: dict[str, asyncio.Task[None]]` and make `_track_execution_task()` register the task under its job ID while retaining the existing cleanup set.

Implement:

```python
async def cancel_job(self, job_id: str) -> TrackedJob:
    lock = self._job_locks.setdefault(job_id, asyncio.Lock())
    async with lock:
        tracked = await self._job_store.get(job_id)
        if tracked is None:
            raise JobNotFoundError(f"Job not found: {job_id}")
        if tracked.status in {PlanStatus.COMPLETED, PlanStatus.FAILED, PlanStatus.PARTIAL}:
            raise JobNotCancellableError(
                f"Job {job_id} is already {tracked.status.value}",
                remediation=f"acheron job status {job_id}",
            )
        task = self._execution_tasks.get(job_id)
        if task is None:
            raise JobNotCancellableError(
                f"Job {job_id} has no active execution task",
                remediation=f"acheron job status {job_id}",
            )
        task.cancel("cancelled by operator")
        await self._wait_for_background_persists(
            job_id,
            max_wait=self._settings.orchestrator.shutdown_drain_seconds,
            raise_on_timeout=True,
        )
        final = await self._job_store.get(job_id)
        if final is None:
            raise JobNotFoundError(f"Job not found: {job_id}")
        return final
```

In the `CancelledError` path of `_execute()`/`_run_execution()`, call `_record_cancellation(tracked, reason="cancelled by operator")`, retain all existing partial result fields, set `PlanStatus.FAILED`, and call `_persist_shielded()` before re-raising or returning. Keep shutdown cancellation's existing reason separate. Ensure the task cleanup callback removes the job ID only after persistence completes.

- [ ] **Step 4: Run the focused tests and verify they pass**

```bash
uv run pytest tests/shell/test_orchestrator.py -k 'cancel' tests/integration/test_job_lifecycle.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit orchestrator cancellation**

```bash
git add src/acheron/shell/orchestrator.py src/acheron/core/errors.py \
  tests/shell/test_orchestrator.py tests/integration/test_job_lifecycle.py
git commit -m "feat(OPS-008): cancel jobs with partial state"
```

### Task 7: Expose cancellation through API, client, and CLI

**Files:**
- Modify: `src/acheron/shell/api/routes/jobs.py`
- Modify: `src/acheron/api_client.py`
- Modify: `src/acheron/cli.py`
- Modify: `tests/shell/api/test_jobs.py`
- Modify: `tests/test_api_client.py`
- Modify: `tests/shell/test_cli.py`

**Interfaces:**
- Consumes: `Orchestrator.cancel_job()`, `ErrorResponse`, and existing mutation authentication.
- Produces: `POST /jobs/{job_id}/cancel`, `AcheronClient.cancel_job(job_id)`, and `acheron job cancel ID`.

- [ ] **Step 1: Write failing route/client/CLI tests**

```python
async def test_cancel_route_returns_failed_job(client, active_job) -> None:
    response = await client.post("/jobs/job-1/cancel", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["errors"][0]["message"] == "cancelled by operator"


def test_cancel_cli_returns_success(runner, client) -> None:
    result = runner.invoke(main, ["job", "cancel", "job-1"])
    assert result.exit_code == 0
    assert "cancelled" in result.output.lower()
```

Add a client test asserting the bearer header is sent on `POST /jobs/job-1/cancel`.

- [ ] **Step 2: Run tests and verify failure**

```bash
uv run pytest tests/shell/api/test_jobs.py tests/test_api_client.py tests/shell/test_cli.py -k cancel -q
```

Expected: FAIL because the route, client method, and command do not exist.

- [ ] **Step 3: Implement the public cancellation surface**

Add the route:

```python
@router.post("/{job_id}/cancel", response_model=JobResponse)
async def cancel_job(
    job_id: str,
    orch: OrchestratorDep,
    _token: RegistrationTokenDep,
) -> JobResponse:
    try:
        return _tracked_to_response(await orch.cancel_job(job_id))
    except AcheronError as exc:
        raise HTTPException(
            status_code=409 if isinstance(exc, JobNotCancellableError) else 404,
            detail=ErrorResponse(
                type=type(exc).__name__,
                message=str(exc),
                remediation=exc.remediation,
            ).model_dump(),
        ) from exc
```

Add `AcheronClient.cancel_job()` using `_mutation_headers()` and add the Click command that prints the returned status. Route domain errors through the structured error renderer and return exit code 1 for failures.

- [ ] **Step 4: Run focused tests and verify pass**

```bash
uv run pytest tests/shell/api/test_jobs.py tests/test_api_client.py tests/shell/test_cli.py -k cancel -q
```

Expected: PASS.

- [ ] **Step 5: Commit public cancellation**

```bash
git add src/acheron/shell/api/routes/jobs.py src/acheron/api_client.py src/acheron/cli.py \
  tests/shell/api/test_jobs.py tests/test_api_client.py tests/shell/test_cli.py
git commit -m "feat(OPS-008): expose job cancellation"
```

### Stage 3 checkpoint

- [ ] Run:

```bash
uv run pytest tests/shell/test_orchestrator.py tests/shell/api/test_jobs.py \
  tests/test_api_client.py tests/shell/test_cli.py tests/integration/test_job_lifecycle.py -q
```

- [ ] Verify a cancelled job remains `FAILED` after a delayed persistence callback.
- [ ] Verify `acheron job cancel ID` exits 0 for an active job and 1 for a terminal job.

---

## Stage 4 — Control and recovery (`OPS-009`, `OPS-020`, `OPS-021`, `OPS-027`)

### Task 8: Implement retry as a linked fresh submission

**Files:**
- Modify: `src/acheron/shell/orchestrator.py`
- Modify: `src/acheron/shell/api/routes/jobs.py`
- Modify: `src/acheron/api_client.py`
- Modify: `src/acheron/cli.py`
- Modify: `tests/shell/test_orchestrator.py`
- Modify: `tests/shell/api/test_jobs.py`
- Modify: `tests/test_api_client.py`
- Modify: `tests/shell/test_cli.py`
- Modify: `tests/integration/test_job_lifecycle.py`

**Interfaces:**
- Consumes: stored `TrackedJob.request`, `RetryJobRequest`, normal request validation, and `Orchestrator.submit_job()`.
- Produces: `POST /jobs/{job_id}/retry`, `AcheronClient.retry_job()`, and `acheron job retry ID` returning a new linked job.

- [ ] **Step 1: Write failing retry tests**

```python
@pytest.mark.asyncio
async def test_retry_creates_new_job_with_override(orchestrator, failed_job) -> None:
    retried = await orchestrator.submit_retry(
        failed_job.job_id,
        request=AudioRequest("/data/book.wav", "en", "es", "whisper-tiny"),
        strategy=ExecutorStrategy.STREAMING,
        label="atlas-retry",
    )

    assert retried.job_id != failed_job.job_id
    assert retried.retries_from == failed_job.job_id
    assert retried.request.asr_model == "whisper-tiny"
    original = await orchestrator.get_job(failed_job.job_id)
    assert original is not None
    assert original.retries_from is None
```

Add route/client/CLI tests that a retry request with only `asr_model` reuses the original source/language values and returns a different job ID.

- [ ] **Step 2: Run focused tests and verify failure**

```bash
uv run pytest tests/shell/test_orchestrator.py tests/shell/api/test_jobs.py \
  tests/test_api_client.py tests/shell/test_cli.py tests/integration/test_job_lifecycle.py -k retry -q
```

Expected: FAIL because retry has no orchestrator or public implementation.

- [ ] **Step 3: Implement the linked retry path**

Add an orchestrator operation with an explicit signature:

```python
async def submit_retry(
    self,
    source_job_id: str,
    request: JobRequest,
    strategy: ExecutorStrategy,
    *,
    label: str | None,
) -> TrackedJob:
    source = await self._job_store.get(source_job_id)
    if source is None:
        raise JobNotFoundError(f"Job not found: {source_job_id}")
    return await self.submit_job(
        request,
        strategy,
        label=label,
        retries_from=source_job_id,
    )
```

In the route, load the source job, merge each explicitly supplied `RetryJobRequest` field into its stored request, resolve an overridden source path through `_resolve_submission_source()`, and reuse `_build_job_request()` validation. Do not reuse the old plan or step cache. Add `AcheronClient.retry_job()` with a strict JSON body and a CLI command with `--src`, `--dest`, `--asr`, and `--label` overrides.

- [ ] **Step 4: Run focused tests and verify pass**

```bash
uv run pytest tests/shell/test_orchestrator.py tests/shell/api/test_jobs.py \
  tests/test_api_client.py tests/shell/test_cli.py tests/integration/test_job_lifecycle.py -k retry -q
```

Expected: PASS.

- [ ] **Step 5: Commit retry**

```bash
git add src/acheron/shell/orchestrator.py src/acheron/shell/api/routes/jobs.py \
  src/acheron/api_client.py src/acheron/cli.py tests/shell/test_orchestrator.py \
  tests/shell/api/test_jobs.py tests/test_api_client.py tests/shell/test_cli.py \
  tests/integration/test_job_lifecycle.py
git commit -m "feat(OPS-009): add linked job retry"
```

### Task 9: Add resume remediation and selective cache invalidation

**Files:**
- Modify: `src/acheron/core/errors.py`
- Modify: `src/acheron/shell/cache.py`
- Modify: `src/acheron/shell/orchestrator.py`
- Modify: `src/acheron/shell/api/routes/jobs.py`
- Modify: `src/acheron/api_client.py`
- Modify: `src/acheron/cli.py`
- Modify: `tests/shell/test_cache.py`
- Modify: `tests/shell/test_orchestrator.py`
- Modify: `tests/shell/api/test_jobs.py`
- Modify: `tests/test_api_client.py`
- Modify: `tests/shell/test_cli.py`

**Interfaces:**
- Consumes: `ResumeJobRequest`, `Plan.steps`, existing `StepCache` manifests, and current `resume_job()` flow.
- Produces: `StepCache.invalidate_steps(job_id, step_ids)`, `Orchestrator.resume_job(job_id, *, invalidate_steps, invalidate_chapters)`, structured remediation for `OPS-020`/`OPS-021`, and the replacement resume CLI options.

- [ ] **Step 1: Write failing cache and remediation tests**

```python
@pytest.mark.asyncio
async def test_invalidate_steps_removes_selected_dependents(step_cache, tmp_path) -> None:
    await step_cache.save_outputs("job-1", "step-46", outputs_for("step-46"))
    await step_cache.save_outputs("job-1", "step-47", outputs_for("step-47"))
    await step_cache.save_outputs("job-1", "step-48", outputs_for("step-48"))

    await step_cache.invalidate_steps("job-1", {"step-47", "step-48"})

    assert await step_cache.step_has_valid_cache("job-1", "step-46")
    assert not await step_cache.step_has_valid_cache("job-1", "step-47")
    assert not await step_cache.step_has_valid_cache("job-1", "step-48")
```

Add an orchestrator test with a plan DAG proving selecting `step-47` invalidates its transitive descendants but not unrelated `step-46`. Add route/CLI tests asserting:

```python
assert error_body == {
    "type": "JobAlreadyRunningError",
    "message": "Job job-1 is already running",
    "remediation": "acheron job cancel job-1",
}
```

and:

```python
assert "Try: acheron job submit" in result.output
```

for a job whose saved plan is missing.

- [ ] **Step 2: Run focused tests and verify failure**

```bash
uv run pytest tests/shell/test_cache.py tests/shell/test_orchestrator.py \
  tests/shell/api/test_jobs.py tests/test_api_client.py tests/shell/test_cli.py \
  -k 'resume or invalidate or already_running or no_plan' -q
```

Expected: FAIL because resume still accepts `force_fresh`, deletes the whole job cache, and emits unstructured errors.

- [ ] **Step 3: Implement targeted invalidation**

Add to both `StepCache` and `InMemoryStepCache`:

```python
async def invalidate_steps(self, job_id: str, step_ids: Collection[str]) -> None:
    for step_id in step_ids:
        step_dir = self._data_dir / job_id / step_id
        await asyncio.to_thread(shutil.rmtree, step_dir, ignore_errors=True)
```

In the orchestrator, resolve requested chapters by matching the plan step payload's `chapter_id` value, reject unknown step IDs or chapters with `InvalidationTargetError`, and compute the transitive descendant closure from `PlanStep.depends_on`. Pass that closure to `invalidate_steps()` before scheduling the resumed execution. Remove `force_fresh` from the orchestrator, client, route body, and CLI.

Replace the bare no-plan exception with `NoPlanToResumeError` and give it remediation text containing `acheron job submit`. Set `JobAlreadyRunningError.remediation` to `acheron job cancel <id>`. Serialize all error responses through `ErrorResponse` and update `_http_error_detail()` / `_parse_remote_error()` to parse its JSON shape.

- [ ] **Step 4: Implement the new resume request and CLI**

Use this client signature:

```python
async def resume_job(
    self,
    job_id: str,
    *,
    invalidate_steps: Sequence[str] = (),
    invalidate_chapters: Sequence[int] = (),
) -> JobResponse:
```

The route passes `ResumeJobRequest.invalidate_steps` and `.invalidate_chapters` directly to the orchestrator. The CLI declares repeatable Click options:

```python
@click.option("--invalidate-step", "invalidate_steps", multiple=True)
@click.option("--invalidate-chapter", "invalidate_chapters", type=int, multiple=True)
def resume(job_id: str, invalidate_steps: tuple[str, ...], invalidate_chapters: tuple[int, ...]) -> None:
    response = _run(
        _get_client().resume_job(
            job_id,
            invalidate_steps=invalidate_steps,
            invalidate_chapters=invalidate_chapters,
        )
    )
    _print_job_summary(response)
```

- [ ] **Step 5: Run focused tests and verify pass**

```bash
uv run pytest tests/shell/test_cache.py tests/shell/test_orchestrator.py \
  tests/shell/api/test_jobs.py tests/test_api_client.py tests/shell/test_cli.py \
  -k 'resume or invalidate or already_running or no_plan' -q
```

Expected: PASS.

- [ ] **Step 6: Commit recovery controls**

```bash
git add src/acheron/core/errors.py src/acheron/shell/cache.py \
  src/acheron/shell/orchestrator.py src/acheron/shell/api/routes/jobs.py \
  src/acheron/api_client.py src/acheron/cli.py tests/shell/test_cache.py \
  tests/shell/test_orchestrator.py tests/shell/api/test_jobs.py \
  tests/test_api_client.py tests/shell/test_cli.py
git commit -m "feat(OPS-027): add selective resume invalidation"
```

### Stage 4 checkpoint

- [ ] Run:

```bash
uv run pytest tests/shell/test_cache.py tests/shell/test_orchestrator.py \
  tests/shell/api/test_jobs.py tests/test_api_client.py tests/shell/test_cli.py \
  tests/integration/test_job_lifecycle.py -q
```

- [ ] Verify retry produces a new ID and leaves `retries_from` on the new job.
- [ ] Verify resume no longer accepts `force_fresh` and only removes selected cache entries plus downstream dependents.
- [ ] Verify running/no-plan resume errors render copy-pasteable remediation.

---

## Stage 5 — Live monitoring (`OPS-002`, `OPS-014`)

### Task 10: Build the bounded per-job progress event broker

**Files:**
- Create: `src/acheron/shell/job_events.py`
- Modify: `src/acheron/shell/orchestrator.py`
- Modify: `src/acheron/core/schemas.py` only if event conversion needs a public-model adjustment
- Modify: `tests/shell/test_orchestrator.py`
- Create: `tests/shell/test_job_events.py`

**Interfaces:**
- Consumes: `JobLogEvent`, `TrackedJob.progress`, existing execution lifecycle hooks, and terminal status transitions.
- Produces: `JobEventBroker.publish(event)`, `JobEventBroker.subscribe(job_id, follow=True)`, `JobEventBroker.finish(job_id)`, and an `Orchestrator.events` property exposing the broker to API routes.

- [ ] **Step 1: Write failing broker tests**

```python
@pytest.mark.asyncio
async def test_subscriber_receives_snapshot_and_terminal_event() -> None:
    broker = JobEventBroker(max_events=8)
    snapshot = event("job-1", "running", "step-1")
    terminal = event("job-1", "completed", None)

    await broker.publish(snapshot)
    stream = broker.subscribe("job-1")
    await broker.publish(terminal)
    await broker.finish("job-1")

    assert [item.status async for item in stream] == [PlanStatus.RUNNING, PlanStatus.COMPLETED]
```

Add a bounded-buffer test proving old events are dropped when the configured limit is reached and an unknown job has no subscription.

- [ ] **Step 2: Run tests and verify failure**

```bash
uv run pytest tests/shell/test_job_events.py tests/shell/test_orchestrator.py -k event -q
```

Expected: FAIL because the broker does not exist.

- [ ] **Step 3: Implement the broker**

Create `JobEventBroker` with per-job deques and subscriber queues. `publish()` stores the event and sends it to active subscribers; `subscribe(job_id, follow=True)` yields buffered events first, then waits for new events when `follow` is true; `finish()` sends a sentinel after the terminal event and removes the active queue. Bound the deque to `max_events` and use an `asyncio.Lock` around queue registration and publication.

Instantiate the broker in `Orchestrator.__init__`. Publish events from the existing progress handler at step start/completion/failure, from operator cancellation, and after terminal reconciliation. The broker does not replace `JobStore`; it only observes the durable state.

- [ ] **Step 4: Run tests and verify pass**

```bash
uv run pytest tests/shell/test_job_events.py tests/shell/test_orchestrator.py -k event -q
```

Expected: PASS.

- [ ] **Step 5: Commit the broker**

```bash
git add src/acheron/shell/job_events.py src/acheron/shell/orchestrator.py \
  src/acheron/core/schemas.py tests/shell/test_job_events.py tests/shell/test_orchestrator.py
git commit -m "feat(OPS-014): add job progress event broker"
```

### Task 11: Expose NDJSON job logs through the API and client

**Files:**
- Modify: `src/acheron/shell/api/routes/jobs.py`
- Modify: `src/acheron/api_client.py`
- Modify: `tests/shell/api/test_jobs.py`
- Modify: `tests/test_api_client.py`

**Interfaces:**
- Consumes: `JobEventBroker.subscribe()`, `JobLogEvent.model_dump_json()`, and `AcheronClient` transport configuration.
- Produces: `GET /jobs/{job_id}/logs?follow=true` and `AcheronClient.tail_job(job_id) -> AsyncIterator[JobLogEvent]`.

- [ ] **Step 1: Write failing stream tests**

Add a route test that reads the response body as lines and validates each line with `JobLogEvent.model_validate_json()`:

```python
async def test_job_logs_are_newline_delimited_json(client, active_job) -> None:
    async with client.stream("GET", "/jobs/job-1/logs?follow=true") as response:
        lines = [line async for line in response.aiter_lines()]

    assert lines
    assert all(JobLogEvent.model_validate_json(line).job_id == "job-1" for line in lines)
    assert JobLogEvent.model_validate_json(lines[-1]).status in {
        PlanStatus.COMPLETED,
        PlanStatus.FAILED,
        PlanStatus.PARTIAL,
    }
```

Add a client test using an `httpx.MockTransport` stream and assert the async iterator yields two typed events.

- [ ] **Step 2: Run tests and verify failure**

```bash
uv run pytest tests/shell/api/test_jobs.py tests/test_api_client.py -k 'logs or tail or stream' -q
```

Expected: FAIL because the route and client iterator do not exist.

- [ ] **Step 3: Implement the streaming route**

Add a `StreamingResponse` route with `media_type="application/x-ndjson"`:

```python
@router.get("/{job_id}/logs")
async def job_logs(
    job_id: str,
    orch: OrchestratorDep,
    follow: bool = True,
) -> StreamingResponse:
    if await orch.get_job(job_id) is None:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                type="JobNotFoundError",
                message=f"Job not found: {job_id}",
                remediation="acheron jobs",
            ).model_dump(),
        )

    async def body() -> AsyncIterator[bytes]:
        async for event in orch.events.subscribe(job_id, follow=follow):
            yield event.model_dump_json().encode() + b"\n"

    return StreamingResponse(body(), media_type="application/x-ndjson")
```

`subscribe()` emits the latest snapshot first, then live events, and stops after the terminal event. If `follow=false`, emit the current snapshot and close. Every event message passes through the existing sanitization path before publication.

Implement `AcheronClient.tail_job()` as an async generator that keeps the `httpx.AsyncClient.stream()` context open for the duration of iteration and parses each non-empty line with `JobLogEvent.model_validate_json()`.

- [ ] **Step 4: Run tests and verify pass**

```bash
uv run pytest tests/shell/api/test_jobs.py tests/test_api_client.py -k 'logs or tail or stream' -q
```

Expected: PASS.

- [ ] **Step 5: Commit the log stream**

```bash
git add src/acheron/shell/api/routes/jobs.py src/acheron/api_client.py \
  tests/shell/api/test_jobs.py tests/test_api_client.py
git commit -m "feat(OPS-014): stream job progress logs"
```

### Task 12: Add follow, watch, and tail CLI surfaces

**Files:**
- Modify: `src/acheron/cli.py`
- Modify: `tests/shell/test_cli.py`
- Modify: `tests/integration/test_job_lifecycle.py`

**Interfaces:**
- Consumes: `AcheronClient.get_job()`, `AcheronClient.tail_job()`, Rich `Live`, `JobResponse.progress`, and terminal `PlanStatus` values.
- Produces: `acheron job watch ID`, `acheron job submit --follow`, and `acheron job tail ID` with defined exit codes.

- [ ] **Step 1: Write failing CLI monitoring tests**

Add a deterministic polling fixture that returns running then completed responses:

```python
def test_job_watch_exits_zero_on_completion(runner, client) -> None:
    result = runner.invoke(main, ["job", "watch", "job-1"])
    assert result.exit_code == 0
    assert "2/2" in result.output


def test_job_watch_exits_one_on_failure(runner, client) -> None:
    result = runner.invoke(main, ["job", "watch", "job-1"])
    assert result.exit_code == 1
    assert "failed" in result.output.lower()


def test_job_tail_renders_each_event(runner, client) -> None:
    result = runner.invoke(main, ["job", "tail", "job-1"])
    assert result.exit_code == 0
    assert "step-3" in result.output
```

Add a submit test proving `--follow` first prints the submitted ID, then enters the same watch renderer.

- [ ] **Step 2: Run tests and verify failure**

```bash
uv run pytest tests/shell/test_cli.py tests/integration/test_job_lifecycle.py -k 'watch or follow or tail' -q
```

Expected: FAIL because the commands and renderer do not exist.

- [ ] **Step 3: Implement one shared polling renderer**

Create a private helper with an exact terminal contract:

```python
def _watch_job(client: AcheronClient, job_id: str) -> int:
    with Live(console=console, refresh_per_second=4) as live:
        while True:
            job = _run(client.get_job(job_id))
            live.update(_job_progress_renderable(job))
            if job.status is PlanStatus.COMPLETED:
                return 0
            if job.status in {PlanStatus.FAILED, PlanStatus.PARTIAL}:
                return 1
            time.sleep(2)
```

Use `JobProgress` for the bar and current-step text, show `eta_seconds` when non-null, and render the first error message when present. Inject the sleep function or patch it in tests so unit tests do not wait two real seconds per poll.

Add `job watch` calling `_watch_job()`. Add `--follow` to submit; after printing the submitted response, call `_watch_job()` with the new ID and raise `click.exceptions.Exit(code)` using its result. Add `job tail` that consumes `tail_job()` through `_run()` in a dedicated async consumer, prints one line per event, and returns 0 after the terminal event. Catch local `KeyboardInterrupt` without calling `cancel_job()`.

- [ ] **Step 4: Run tests and verify pass**

```bash
uv run pytest tests/shell/test_cli.py tests/integration/test_job_lifecycle.py -k 'watch or follow or tail' -q
```

Expected: PASS.

- [ ] **Step 5: Commit CLI monitoring**

```bash
git add src/acheron/cli.py tests/shell/test_cli.py tests/integration/test_job_lifecycle.py
git commit -m "feat(OPS-002): add job follow and watch"
```

### Stage 5 checkpoint

- [ ] Run:

```bash
uv run pytest tests/shell/test_job_events.py tests/shell/api/test_jobs.py \
  tests/test_api_client.py tests/shell/test_cli.py tests/integration/test_job_lifecycle.py -q
```

- [ ] Verify a stream sends a terminal event and closes.
- [ ] Verify follow/watch exit codes are 0 for completion and 1 for failure/partial.
- [ ] Verify local observer interruption never calls the cancellation endpoint.

---

## Final integration and verification

### Task 13: Add complete Phase 4C journeys and update UX evidence

**Files:**
- Modify: `tests/integration/conftest.py`
- Modify: `tests/integration/test_job_lifecycle.py`
- Modify: `tests/integration/test_multi_job.py` if label filtering needs multi-job coverage
- Modify: applicable `tests/first_run/test_3_success_criteria.py`
- Modify: `docs/ux_review/ops.md`
- Modify: `docs/ux_review/summary.md`

**Interfaces:**
- Consumes: all completed Phase 4C API, client, CLI, dashboard, cancellation, recovery, and event surfaces.
- Produces: story-scoped verification evidence and updated UX metadata.

- [ ] **Step 1: Write failing end-to-end journey tests**

Add one integration test per behavior cluster:

```python
@pytest.mark.asyncio
async def test_failed_job_is_diagnosable_from_status_and_dashboard(
    failing_orchestrator,
    tmp_path: Path,
) -> None:
    source = tmp_path / "book.epub"
    source.touch()
    tracked = await failing_orchestrator.submit_job(
        EpubRequest(str(source), "en", "es"),
        ExecutorStrategy.STREAMING,
    )
    failed = await wait_for_terminal(failing_orchestrator, tracked.job_id)

    assert failed.status is PlanStatus.FAILED
    assert failed.result is not None
    assert failed.result.errors[0].step_id == "step-2"
    assert failed.result.errors[0].worker_id == "chunking-local"
    assert failed.result.outputs == ()


@pytest.mark.asyncio
async def test_cancel_retry_and_selective_resume_are_distinct(
    blocking_orchestrator,
    tmp_path: Path,
) -> None:
    source = tmp_path / "book.epub"
    source.touch()
    original = await blocking_orchestrator.submit_job(
        EpubRequest(str(source), "en", "es"),
        ExecutorStrategy.STREAMING,
    )
    await blocking_orchestrator.handler.started.wait()
    cancelled = await blocking_orchestrator.cancel_job(original.job_id)
    audio_source = tmp_path / "book.wav"
    audio_source.touch()
    retried = await blocking_orchestrator.submit_retry(
        cancelled.job_id,
        AudioRequest(str(audio_source), "en", "es", "whisper-tiny"),
        ExecutorStrategy.STREAMING,
        label="retry",
    )
    resumed = await blocking_orchestrator.resume_job(
        retried.job_id,
        invalidate_steps=("step-47",),
        invalidate_chapters=(),
    )

    assert cancelled.job_id != retried.job_id
    assert retried.retries_from == cancelled.job_id
    assert resumed.job_id == retried.job_id
```

Add `failing_orchestrator` and `blocking_orchestrator` fixtures to `tests/integration/conftest.py`, using the existing temporary `PlanCache`, `StepCache`, worker registry, and handler fixtures. Add a `wait_for_terminal(orchestrator, job_id)` helper that polls `get_job()` with `asyncio.sleep(0)` until the status is terminal and fails after a bounded 100 iterations. Add a live-monitoring journey that consumes at least one progress event and verifies the terminal event. Extend first-run coverage only if the existing Compose stack can exercise the dashboard detail path without introducing a separate deployment fixture.

- [ ] **Step 2: Run the focused integration tests and verify failure**

```bash
uv run pytest tests/integration/test_job_lifecycle.py tests/integration/test_multi_job.py -q
```

Expected: FAIL until all stage contracts are wired together.

- [ ] **Step 3: Implement the journey assertions and evidence capture**

Use existing fixtures and worker handlers. Do not depend on repository configuration files or hardcoded absolute paths; create input and output files under pytest-provided temporary directories. Keep dashboard assertions in dashboard tests and use integration tests for API/orchestrator lifecycle behavior.

- [ ] **Step 4: Run all final gates**

Run exactly:

```bash
just lint-strict
just type-check
just test
just validate
just ux-validate
just first-run
```

Then run story verification:

```bash
for story in OPS-004 OPS-001 OPS-010 OPS-013 OPS-017 OPS-023 \
  OPS-008 OPS-009 OPS-020 OPS-021 OPS-027 OPS-002 OPS-014; do
  just ux-verify "$story"
done
```

Expected: every command exits 0. If `just first-run` cannot run because Docker is unavailable, record the environmental failure and do not mark deployment-facing evidence verified.

- [ ] **Step 5: Update UX metadata after verification**

For each story, set `fixed_in` to the implementation commit(s), set `verified_in` to the story verification commit, set `last_verified_at` to the verification timestamp, and set `verified_by` to the verifying agent identity. Refresh `docs/ux_review/summary.md` counts and ordering without changing unrelated stories.

- [ ] **Step 6: Run the final documentation and correctness review**

Review the complete diff against `docs/superpowers/specs/2026-07-29-phase-4c-job-visibility-control-design.md`. Confirm:

- all 13 stories have an implementation and verification path;
- `errors` is typed everywhere;
- `force_fresh` is absent from API/client/CLI contracts;
- cancellation preserves partial state and cannot be overwritten by a background task;
- retry creates a new linked job;
- output serving is allowlisted;
- the dashboard and CLI expose the same response data;
- event streaming remains observational and sanitized.

Run `git diff --check`, inspect changed files for stale references, and commit the final evidence update:

```bash
git add tests/integration docs/ux_review/ops.md docs/ux_review/summary.md
git commit -m "docs(phase-4c): record job visibility verification"
```

---

## Completion checklist

- [ ] Stage 1 contract and persistence complete.
- [ ] Stage 2 CLI/dashboard detail complete.
- [ ] Stage 3 cancellation complete.
- [ ] Stage 4 retry and selective resume complete.
- [ ] Stage 5 follow/watch/tail complete.
- [ ] All focused tests pass.
- [ ] `just validate` passes.
- [ ] `just ux-validate` passes.
- [ ] Applicable first-run coverage passes.
- [ ] All 13 `just ux-verify` commands pass.
- [ ] UX metadata and summary are updated.
- [ ] Final correctness and documentation-staleness review is complete.
