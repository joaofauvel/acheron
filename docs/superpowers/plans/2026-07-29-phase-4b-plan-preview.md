# Phase 4B Plan Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add typed plan lookup and non-persisting submission previews for `OPS-011` and `OPS-016` without adding cost estimation or concrete worker assignment.

**Architecture:** Keep `Plan` as the internal immutable dataclass and add typed public response models that expose only operator-relevant structure. Factor request normalization and plan compilation so normal submission and preview share validation, while preview skips plan persistence, job-store writes, and execution. Expose persisted plans through a dedicated `/plans/{plan_id}` route and wire both endpoints through the existing async client and Click CLI.

**Tech Stack:** Python 3.14, FastAPI, Pydantic v2, httpx, Click, Rich, pytest, respx, Just.

## Global Constraints

- Scope is limited to Phase 4B Plan preview: `OPS-011` and `OPS-016`.
- Do not estimate cost, assign concrete worker IDs, or change worker selection/scheduling.
- Preview must share normal submission validation and must not save a plan, persist a job, or schedule execution.
- Do not expose internal `PlanStep.payload` values, including resolved filesystem paths, in the public response or CLI.
- Validate plan IDs before filesystem access so plan lookup cannot escape the configured data directory.
- Use TDD: each behavior change gets a failing test, a minimal implementation, and a passing focused test before the next behavior.
- Add no dependencies, type ignores, compatibility fallbacks, or unrelated Phase 4C behavior.
- Preserve existing TLS, bearer-token, input-upload, error-sanitization, and normal submission behavior.
- Run `just lint-strict`, `just type-check`, `just test`, `just validate`, and `just ux-validate` before completion.

---

## File Map

- Modify `src/acheron/core/schemas.py`: add public `PlanStepResponse` and `PlanResponse` models and conversion from internal `Plan`.
- Modify `src/acheron/shell/cache.py`: centralize safe plan-path construction and reject unsafe plan IDs before filesystem access.
- Modify `src/acheron/shell/orchestrator.py`: share plan compilation between submission and preview; add preview and persisted-plan lookup methods.
- Modify `src/acheron/shell/api/routes/jobs.py`: factor request normalization, add `POST /jobs:preview`, and return the typed plan response.
- Create `src/acheron/shell/api/routes/plans.py`: add `GET /plans/{plan_id}` and map cache misses/corruption to safe HTTP responses.
- Modify `src/acheron/shell/api/app.py`: register the plans router.
- Modify `src/acheron/api_client.py`: add `preview_job()` and `get_plan()`.
- Modify `src/acheron/cli.py`: add `--dry-run`, plan rendering, and `job plan` lookup by plan ID or job ID.
- Modify `tests/core/test_schemas.py`: cover plan response serialization and payload omission.
- Modify `tests/shell/test_cache.py`: cover unsafe plan IDs.
- Modify `tests/shell/test_orchestrator.py`: cover preview compilation without persistence or execution.
- Modify `tests/shell/api/test_jobs.py`: cover preview validation and no-persistence behavior.
- Create `tests/shell/api/test_plans.py`: cover persisted plan lookup and error mapping.
- Modify `tests/shell/api/test_schemas.py`: cover public response-schema exports.
- Modify `tests/test_api_client.py`: cover preview/lookup request contracts and authentication.
- Modify `tests/shell/test_cli.py`: cover plan lookup, job-ID lookup, dry-run output, and no `/jobs` call.
- Modify `docs/ux_review/ops.md`: record fixed and verified metadata for `OPS-011` and `OPS-016` after implementation verification.

---

### Task 1: Add the typed public plan response contract

**Files:**
- Modify: `src/acheron/core/schemas.py`
- Modify: `tests/core/test_schemas.py`
- Modify: `tests/shell/api/test_schemas.py`

**Interfaces:**
- Consumes: `Plan`, `PlanStep`, `ExecutorStrategy`, `StepStatus`, and `WorkerType` from `acheron.core.models`.
- Produces: `PlanStepResponse` and `PlanResponse`, each with a `from_plan()` conversion that omits internal payloads.

- [ ] **Step 1: Write the failing schema tests**

Add a fixture-level plan with one extraction step whose payload contains an internal source path, then assert:

```python
from acheron.core.models import ExecutorStrategy, Plan, PlanStep, StepStatus, WorkerType
from acheron.core.schemas import PlanResponse


def test_plan_response_exposes_structure_without_internal_payload() -> None:
    plan = Plan(
        plan_id="plan-1",
        job_id="job-1",
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
                payload={"source_path": "/data/inputs/book.epub"},
            ),
        ),
    )

    response = PlanResponse.from_plan(plan)

    assert response.plan_id == "plan-1"
    assert response.steps[0].worker_type is WorkerType.EXTRACTION
    assert response.steps[0].depends_on == []
    assert response.steps[0].status is StepStatus.PENDING
    assert "payload" not in response.model_dump()
    assert "/data/inputs/book.epub" not in response.model_dump_json()
```

Add the public-import assertion:

```python
def test_plan_response_models_keep_their_public_import_path() -> None:
    from acheron.core.schemas import PlanResponse, PlanStepResponse
    from acheron.shell.api import schemas

    assert schemas.PlanResponse is PlanResponse
    assert schemas.PlanStepResponse is PlanStepResponse
```

- [ ] **Step 2: Run the focused tests and verify the expected failure**

Run:

```bash
uv run pytest tests/core/test_schemas.py tests/shell/api/test_schemas.py -q
```

Expected: collection or assertion failure because the plan response models and conversion do not exist.

- [ ] **Step 3: Implement the minimal response models**

In `src/acheron/core/schemas.py`, add:

```python
from acheron.core.models import ExecutorStrategy, Plan, StepStatus, WorkerType


class PlanStepResponse(BaseModel):
    """Public structure for one planned pipeline step."""

    step_id: str
    worker_type: WorkerType
    depends_on: list[str]
    status: StepStatus


class PlanResponse(BaseModel):
    """Public operator-facing representation of a compiled plan."""

    plan_id: str
    job_id: str
    source_type: str
    source_language: str
    target_language: str
    executor_strategy: ExecutorStrategy
    steps: list[PlanStepResponse]

    @classmethod
    def from_plan(cls, plan: Plan) -> "PlanResponse":
        return cls(
            plan_id=plan.plan_id,
            job_id=plan.job_id,
            source_type=plan.source_type,
            source_language=plan.source_language,
            target_language=plan.target_language,
            executor_strategy=plan.executor_strategy,
            steps=[
                PlanStepResponse(
                    step_id=step.step_id,
                    worker_type=step.type,
                    depends_on=list(step.depends_on),
                    status=step.status,
                )
                for step in plan.steps
            ],
        )
```

Re-export both models from `src/acheron/shell/api/schemas.py` and add them to its `__all__` list.

- [ ] **Step 4: Run the focused tests and verify they pass**

Run:

```bash
uv run pytest tests/core/test_schemas.py tests/shell/api/test_schemas.py -q
```

Expected: all tests in both files pass.

- [ ] **Step 5: Commit the response contract**

```bash
git add src/acheron/core/schemas.py src/acheron/shell/api/schemas.py \
  tests/core/test_schemas.py tests/shell/api/test_schemas.py
git commit -m "feat(OPS-011): add typed plan response"
```

---

### Task 2: Make plan loading safe and expose orchestrator plan operations

**Files:**
- Modify: `src/acheron/shell/cache.py`
- Modify: `src/acheron/shell/orchestrator.py`
- Modify: `tests/shell/test_cache.py`
- Modify: `tests/shell/test_orchestrator.py`
- Modify: `tests/shell/api/test_jobs.py`

**Interfaces:**
- Consumes: `PlanCache`, `compile_plan`, `TrackedJob`, worker registry capabilities, and `PlanResponse.from_plan()`.
- Produces: `Orchestrator.preview_job(request, strategy) -> Plan`, `Orchestrator.get_plan(plan_id) -> Plan`, and safe plan-cache lookup.

- [ ] **Step 1: Write failing cache-safety and orchestrator tests**

Add a cache test that places a plan outside the configured cache root and proves an unsafe identifier cannot load it:

```python
def test_load_rejects_plan_id_path_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "escaped-plan"
    outside.mkdir()
    (outside / "plan.json").write_text("{}")

    with pytest.raises(CacheMissError):
        PlanCache(tmp_path).load_plan("../escaped-plan")
```

Add an orchestrator test that compiles a valid EPUB request after `start()` and proves preview does not create a job record or plan file:

```python
@pytest.mark.asyncio
async def test_preview_job_compiles_without_persistence(tmp_path: Path) -> None:
    registry = InMemoryWorkerStore()
    await registry.register("tts-1", "http://127.0.0.1:1", "http", tts_caps())
    await registry.register("trans-1", "http://127.0.0.1:2", "http", translation_caps())
    jobs = InMemoryJobStore()
    cache = PlanCache(tmp_path)
    orch = Orchestrator(registry, cache, job_store=jobs)
    await orch.start()
    try:
        plan = await orch.preview_job(
            EpubRequest("/input/book.epub", "en", "es"),
            ExecutorStrategy.STREAMING,
        )
        assert plan.steps
        assert await jobs.list_all() == ()
        assert not cache.plan_exists(plan.plan_id)
    finally:
        await orch.shutdown()
        await orch.close()
```

- [ ] **Step 2: Run the focused tests and verify the expected failure**

Run:

```bash
uv run pytest tests/shell/test_cache.py::test_load_rejects_plan_id_path_escape \
  tests/shell/test_orchestrator.py::test_preview_job_compiles_without_persistence -q
```

Expected: the cache test exposes unsafe path handling or the orchestrator test fails because `preview_job()` does not exist.

- [ ] **Step 3: Implement safe plan paths**

In `PlanCache`, add one private path helper used by `save_plan`, `load_plan`, and `plan_exists`:

```python
_PLAN_ID_RE = re.compile(r"\Aplan-[0-9a-f]+\Z")


def _plan_file(self, plan_id: str) -> Path:
    if _PLAN_ID_RE.fullmatch(plan_id) is None:
        raise CacheMissError(f"Plan not found: {plan_id}")
    return self._data_dir / plan_id / "plan.json"
```

Use `_plan_file(plan.plan_id)` for saving and `_plan_file(plan_id)` for loading/existence checks. Keep generated plan IDs compatible with the expression and preserve existing `CacheMissError` behavior for ordinary missing plans.

- [ ] **Step 4: Refactor shared plan compilation and add orchestrator methods**

In `Orchestrator`, extract the capability lookup and `compile_plan` call from `submit_job` into a private async helper with this shape:

```python
async def _compile_plan(
    self,
    request: JobRequest,
    strategy: ExecutorStrategy,
    *,
    job_id: str | None = None,
) -> Plan:
    capabilities = tuple(w.capabilities for w in await self._registry.list_all())
    return compile_plan(
        request,
        strategy,
        capabilities,
        job_id=job_id,
        chunking=ChunkingLimits(
            max_chunk_length=self._settings.workers.chunking.max_chunk_length,
            chars_per_token=self._settings.chars_per_token,
        ),
    )
```

Keep normal submission behavior unchanged: it generates a job ID, calls `_compile_plan`, saves the returned plan, persists `TrackedJob`, and schedules `_execute`.

Add:

```python
async def preview_job(self, request: JobRequest, strategy: ExecutorStrategy) -> Plan:
    """Compile a plan without persisting or executing a job."""
    if not self._started:
        msg = "Orchestrator.start() must be called before preview_job()"
        raise RuntimeError(msg)
    return await self._compile_plan(request, strategy)

async def get_plan(self, plan_id: str) -> Plan:
    """Load a persisted plan without exposing the cache implementation."""
    return await asyncio.to_thread(self._cache.load_plan, plan_id)
```

Do not call `save_plan`, `_job_store.put`, `_track_execution_task`, or `_invalidate_handler_cache` from `preview_job`.

- [ ] **Step 5: Run the focused foundation tests and verify they pass**

Run:

```bash
uv run pytest tests/shell/test_cache.py::test_load_rejects_plan_id_path_escape \
  tests/shell/test_orchestrator.py::test_preview_job_compiles_without_persistence -q
```

Expected: both foundation tests pass before moving to the API route task.

- [ ] **Step 6: Commit the cache and orchestrator foundation**

```bash
git add src/acheron/shell/cache.py src/acheron/shell/orchestrator.py \
  tests/shell/test_cache.py tests/shell/api/test_jobs.py
git commit -m "feat(OPS-016): add non-persisting plan compilation"
```

---

### Task 3: Add the preview and persisted-plan API routes

**Files:**
- Modify: `src/acheron/shell/api/routes/jobs.py`
- Create: `src/acheron/shell/api/routes/plans.py`
- Modify: `src/acheron/shell/api/app.py`
- Modify: `tests/shell/api/test_jobs.py`
- Create: `tests/shell/api/test_plans.py`

**Interfaces:**
- Consumes: `SubmitJobRequest`, `_resolve_submission_source`, `Orchestrator.preview_job()`, `Orchestrator.get_plan()`, and `PlanResponse.from_plan()`.
- Produces: `POST /jobs:preview` and `GET /plans/{plan_id}`.

- [ ] **Step 1: Write failing route tests**

Add preview validation tests:

```python
@pytest.mark.asyncio
async def test_preview_rejects_audio_without_asr(client) -> None:  # type: ignore[no-untyped-def]
    response = await client.post(
        "/jobs:preview",
        json={
            "source_type": "audio",
            "source_path": "input/book.epub",
            "source_language": "en",
            "target_language": "es",
        },
    )
    assert response.status_code == 422
    assert "asr_model is required" in response.json()["detail"]
```

Add persisted-plan lookup tests using `PlanCache(tmp_path).save_plan(_sample_plan())`:

```python
@pytest.mark.asyncio
async def test_get_plan_returns_public_structure(client, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    PlanCache(tmp_path).save_plan(_sample_plan("plan-1"))

    response = await client.get("/plans/plan-1")

    assert response.status_code == 200
    body = response.json()
    assert body["plan_id"] == "plan-1"
    assert body["steps"][0]["worker_type"] == "extraction"
    assert "payload" not in body["steps"][0]
```

Add missing and unsafe lookup cases:

```python
@pytest.mark.asyncio
@pytest.mark.parametrize("plan_id", ["missing", "../escaped-plan", "plan-../escape"])
async def test_get_plan_returns_not_found_for_invalid_or_missing_id(client, plan_id: str) -> None:  # type: ignore[no-untyped-def]
    response = await client.get(f"/plans/{plan_id}")
    assert response.status_code == 404
```

- [ ] **Step 2: Run the focused route tests and verify the expected failure**

Run:

```bash
uv run pytest tests/shell/api/test_jobs.py -k 'preview' \
  tests/shell/api/test_plans.py -q
```

Expected: preview and plan route tests fail because the routes are not registered.

- [ ] **Step 3: Factor request normalization in the jobs route**

Extract the existing source-type, ASR, strategy, and source-path handling from `submit_job` into a helper with this contract:

```python
async def _build_job_request(orch: Orchestrator, body: SubmitJobRequest) -> tuple[JobRequest, ExecutorStrategy]:
    """Validate a submission body and resolve its source path."""
```

The helper must preserve these existing responses:

- invalid executor strategy: HTTP 400 with `Invalid strategy: <value>`;
- invalid source type: HTTP 400;
- EPUB with an ASR model: HTTP 422;
- audio without a non-empty ASR model: HTTP 422;
- missing or unsafe source path: HTTP 422;
- domain plan errors: HTTP 422 with `sanitise_exc_message`.

Use the helper in both normal submission and preview so the two endpoints cannot drift.

- [ ] **Step 4: Add `POST /jobs:preview`**

Add a route before the dynamic job-ID routes:

```python
@router.post(":preview", response_model=PlanResponse)
async def preview_job(
    body: SubmitJobRequest,
    orch: OrchestratorDep,
    _token: RegistrationTokenDep,
) -> PlanResponse:
    request, strategy = await _build_job_request(orch, body)
    try:
        plan = await orch.preview_job(request, strategy)
    except AcheronError as exc:
        raise HTTPException(status_code=422, detail=sanitise_exc_message(exc)) from exc
    return PlanResponse.from_plan(plan)
```

Keep the normal `POST /jobs` route's status code and warning behavior unchanged.

- [ ] **Step 5: Add `GET /plans/{plan_id}` and register it**

Create `src/acheron/shell/api/routes/plans.py`:

```python
from fastapi import APIRouter, HTTPException

from acheron.core.errors import CacheCorruptedError, CacheMissError
from acheron.core.schemas import PlanResponse
from acheron.shell.api.deps import OrchestratorDep

router = APIRouter()


@router.get("/{plan_id}", response_model=PlanResponse)
async def get_plan(plan_id: str, orch: OrchestratorDep) -> PlanResponse:
    try:
        plan = await orch.get_plan(plan_id)
    except CacheMissError as exc:
        raise HTTPException(status_code=404, detail=f"Plan not found: {plan_id}") from exc
    except CacheCorruptedError as exc:
        raise HTTPException(status_code=500, detail="Cached plan could not be loaded") from exc
    return PlanResponse.from_plan(plan)
```

Register with:

```python
app.include_router(plans.router, prefix="/plans", tags=["plans"])
```

- [ ] **Step 6: Run the route tests and verify they pass**

Run:

```bash
uv run pytest tests/shell/api/test_jobs.py tests/shell/api/test_plans.py -q
```

Expected: all preview, validation, persisted-plan, missing-plan, and safe-ID tests pass.

- [ ] **Step 7: Commit the API routes**

```bash
git add src/acheron/shell/api/routes/jobs.py \
  src/acheron/shell/api/routes/plans.py \
  src/acheron/shell/api/app.py \
  tests/shell/api/test_jobs.py tests/shell/api/test_plans.py
git commit -m "feat(OPS-011,OPS-016): expose plan lookup and preview API"
```

---

### Task 4: Add HTTP client support

**Files:**
- Modify: `src/acheron/api_client.py`
- Modify: `tests/test_api_client.py`

**Interfaces:**
- Consumes: `PlanResponse`, existing HTTP/TLS setup, and `_mutation_headers()`.
- Produces: `AcheronClient.preview_job(...) -> PlanResponse` and `AcheronClient.get_plan(plan_id: str) -> PlanResponse`.

- [ ] **Step 1: Write failing client contract tests**

Add a preview request test that checks the exact endpoint, JSON body, parsed response, and bearer header:

```python
@pytest.mark.asyncio
@respx.mock
async def test_preview_job_posts_typed_request_with_bearer_header() -> None:
    route = respx.post("http://test/jobs:preview").mock(
        return_value=httpx.Response(
            200,
            json={
                "plan_id": "plan-preview",
                "job_id": "job-preview",
                "source_type": "epub",
                "source_language": "en",
                "target_language": "es",
                "executor_strategy": "streaming",
                "steps": [
                    {"step_id": "extract", "worker_type": "extraction", "depends_on": [], "status": "pending"}
                ],
            },
        )
    )

    result = await AcheronClient("http://test", registration_token="secret").preview_job(
        source_type="epub",
        source_path="inputs/book.epub",
        source_language="en",
        target_language="es",
    )

    assert result.plan_id == "plan-preview"
    assert route.calls.last.request.headers["authorization"] == "Bearer secret"
    assert route.calls.last.request.url.path == "/jobs:preview"
```

Add a lookup test:

```python
@pytest.mark.asyncio
@respx.mock
async def test_get_plan_round_trips_plan() -> None:
    respx.get("http://test/plans/plan-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "plan_id": "plan-1",
                "job_id": "job-1",
                "source_type": "epub",
                "source_language": "en",
                "target_language": "es",
                "executor_strategy": "streaming",
                "steps": [],
            },
        )
    )

    result = await AcheronClient("http://test").get_plan("plan-1")

    assert result.job_id == "job-1"
```

- [ ] **Step 2: Run the focused client tests and verify the expected failure**

Run:

```bash
uv run pytest tests/test_api_client.py -k 'preview_job or get_plan' -q
```

Expected: failure because the client methods do not exist.

- [ ] **Step 3: Implement the client methods**

Add `PlanResponse` to the schema imports and implement methods using the same `httpx.AsyncClient` construction as existing methods:

```python
async def preview_job(  # noqa: PLR0913
    self,
    source_type: str,
    source_path: str,
    source_language: str,
    target_language: str,
    executor_strategy: str = "streaming",
    asr_model: str | None = None,
) -> PlanResponse:
    payload = {
        "source_type": source_type,
        "source_path": source_path,
        "source_language": source_language,
        "target_language": target_language,
        "executor_strategy": executor_strategy,
        "asr_model": asr_model,
    }
    async with httpx.AsyncClient(
        base_url=self._base_url, transport=self._transport, verify=self._ssl_verify
    ) as client:
        resp = await client.post("/jobs:preview", json=payload, headers=self._mutation_headers())
        resp.raise_for_status()
        return PlanResponse.model_validate(resp.json())

async def get_plan(self, plan_id: str) -> PlanResponse:
    """Retrieve a persisted plan by ID."""
    async with httpx.AsyncClient(
        base_url=self._base_url, transport=self._transport, verify=self._ssl_verify
    ) as client:
        resp = await client.get(f"/plans/{plan_id}")
        resp.raise_for_status()
        return PlanResponse.model_validate(resp.json())
```

- [ ] **Step 4: Run the client tests and verify they pass**

Run:

```bash
uv run pytest tests/test_api_client.py -k 'preview_job or get_plan' -q
```

Expected: both new tests pass.

- [ ] **Step 5: Commit the client support**

```bash
git add src/acheron/api_client.py tests/test_api_client.py
git commit -m "feat(OPS-011,OPS-016): add plan client methods"
```

---

### Task 5: Add CLI plan lookup and dry-run submission

**Files:**
- Modify: `src/acheron/cli.py`
- Modify: `tests/shell/test_cli.py`

**Interfaces:**
- Consumes: `AcheronClient.get_job()`, `AcheronClient.get_plan()`, `AcheronClient.preview_job()`, `InputResponse`, and `PlanResponse`.
- Produces: `acheron job plan PLAN_ID`, `acheron job plan --job JOB_ID`, and `acheron job submit ... --dry-run`.

- [ ] **Step 1: Write failing CLI tests**

Add plan-ID lookup coverage:

```python
@respx.mock
def test_job_plan_by_plan_id() -> None:
    respx.get(f"{_BASE_URL}/plans/plan-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "plan_id": "plan-1",
                "job_id": "job-1",
                "source_type": "epub",
                "source_language": "en",
                "target_language": "es",
                "executor_strategy": "streaming",
                "steps": [
                    {"step_id": "extract", "worker_type": "extraction", "depends_on": [], "status": "pending"}
                ],
            },
        )
    )

    result = CliRunner().invoke(main, ["job", "plan", "plan-1"])

    assert result.exit_code == 0, result.output
    assert "extract" in result.output
    assert "extraction" in result.output
```

Add dry-run coverage with upload and preview routes, while deliberately not mocking `/jobs`:

```python
@respx.mock
def test_submit_dry_run_previews_without_submitting(tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    epub.touch()
    _mock_upload_success()
    preview_route = respx.post(f"{_BASE_URL}/jobs:preview").mock(
        return_value=httpx.Response(
            200,
            json={
                "plan_id": "plan-preview",
                "job_id": "job-preview",
                "source_type": "epub",
                "source_language": "en",
                "target_language": "es",
                "executor_strategy": "streaming",
                "steps": [
                    {"step_id": "extract", "worker_type": "extraction", "depends_on": [], "status": "pending"}
                ],
            },
        )
    )

    result = CliRunner().invoke(main, ["job", "submit", str(epub), "--src", "en", "--dest", "es", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "no job submitted" in result.output.lower()
    assert "plan-preview" in result.output
    assert preview_route.called
```

Add usage tests that both `PLAN_ID` and `--job JOB_ID` work and that supplying neither or both returns a Click usage error.

- [ ] **Step 2: Run the focused CLI tests and verify the expected failure**

Run:

```bash
uv run pytest tests/shell/test_cli.py -k 'job_plan or dry_run' -q
```

Expected: failure because the command, option, and plan renderer do not exist.

- [ ] **Step 3: Add a shared plan renderer**

In `cli.py`, add a helper with a single rendering path for lookup and preview:

```python
def _print_plan(plan: PlanResponse, *, dry_run: bool = False) -> None:
    title = "Plan preview" if dry_run else "Plan"
    console.print(f"{title}: [bold]{plan.plan_id}[/bold]")
    console.print(f"Job: {plan.job_id}")
    console.print(f"Input: {plan.source_type} ({plan.source_language} → {plan.target_language})")
    console.print(f"Strategy: {plan.executor_strategy.value}")
    table = Table(title="Steps")
    table.add_column("Step")
    table.add_column("Worker type")
    table.add_column("Depends on")
    table.add_column("Status")
    for step in plan.steps:
        table.add_row(step.step_id, step.worker_type.value, ", ".join(step.depends_on) or "-", step.status.value)
    console.print(table)
    if dry_run:
        console.print("Dry run complete; no job submitted.")
```

The renderer must use only public response fields and must not print step payloads.

- [ ] **Step 4: Add `job plan`**

Add a command accepting either a positional plan ID or `--job`:

```python
@job.command("plan")
@click.argument("plan_id", required=False)
@click.option("--job", "job_id", default=None, help="Resolve the plan ID from a job")
def show_plan(plan_id: str | None, job_id: str | None) -> None:
    """Show a compiled plan."""
    if (plan_id is None) == (job_id is None):
        raise click.UsageError("provide exactly one plan ID or --job JOB_ID")
    if job_id is not None:
        job_response = _run(_get_client().get_job(job_id))
        if job_response.plan_id is None:
            console.print(f"[red]Job {job_id} has no plan ID.[/red]")
            raise SystemExit(1)
        plan_id = job_response.plan_id
    _print_plan(_run(_get_client().get_plan(plan_id)))
```

- [ ] **Step 5: Add `--dry-run` to `submit`**

Add `@click.option("--dry-run", is_flag=True, help="Preview the plan without submitting a job")` and branch only after the existing upload succeeds:

```python
if dry_run:
    preview = _run(
        _get_client().preview_job(
            source_type=source_type,
            source_path=uploaded.source_path,
            source_language=src,
            target_language=dest,
            executor_strategy=executor,
            asr_model=asr_model,
        ),
        on_http_error=lambda exc: _print_submit_http_error(
            exc,
            source_language=src,
            target_language=dest,
        ),
    )
    _print_plan(preview, dry_run=True)
    return
```

Leave the existing normal `submit_job()` branch unchanged.

- [ ] **Step 6: Run the CLI tests and verify they pass**

Run:

```bash
uv run pytest tests/shell/test_cli.py -k 'job_plan or dry_run' -q
uv run pytest tests/shell/test_cli.py -q
```

Expected: the focused tests and the complete existing CLI test module pass.

- [ ] **Step 7: Commit the CLI behavior**

```bash
git add src/acheron/cli.py tests/shell/test_cli.py
git commit -m "feat(OPS-011,OPS-016): add plan CLI and dry run"
```

---

### Task 6: Update UX metadata and run the full verification gates

**Files:**
- Modify: `docs/ux_review/ops.md`
- Modify: `docs/ux_review/summary.md` only if the UX validation command requires refreshed aggregate counts

**Interfaces:**
- Consumes: merged implementation commit IDs, focused tests, and CLI/API journey behavior.
- Produces: fixed and verified records for `OPS-011` and `OPS-016` with accurate file references and no stale pre-fix claims.

- [ ] **Step 1: Run focused behavior tests and inspect the resulting diff**

Run:

```bash
uv run pytest tests/core/test_schemas.py tests/shell/test_cache.py \
  tests/shell/api/test_schemas.py tests/shell/api/test_jobs.py \
  tests/shell/api/test_plans.py tests/test_api_client.py tests/shell/test_cli.py -q

git diff a9637eb..HEAD --stat
```

Expected: all changed-path tests pass, and the diff contains only Phase 4B plan preview files plus metadata.

- [ ] **Step 2: Run project quality gates**

Run in order:

```bash
just lint-strict
just type-check
just test
just validate
just ux-validate
```

Expected:

- Ruff formatting and lint checks pass.
- Mypy passes for all configured source and test paths.
- The full test suite passes with the repository coverage threshold.
- `just validate` passes all of its chained checks.
- `just ux-validate` reports valid story metadata.

- [ ] **Step 3: Exercise the Phase 4B user journeys**

Against the test app or local simulator, verify:

```bash
acheron job plan plan-<persisted-id>
acheron job plan --job job-<persisted-id>
acheron job submit book.epub --src en --dest es --dry-run
acheron jobs
```

The first two commands display the plan steps. The dry run displays the same plan structure and explicitly says no job was submitted. The final command does not show a job created by the dry run.

- [ ] **Step 4: Update story records**

In `docs/ux_review/ops.md`, update `OPS-011` and `OPS-016` from `open` to `fixed`, replace their pre-fix issue text/file ranges with the implemented API/client/CLI behavior, and set `fixed_in` to the implementation commit IDs. Record the journey evidence and verification commit according to `docs/ux_review/SPEC.md`; do not mark a story verified from file/line evidence alone.

- [ ] **Step 5: Run a fresh correctness and documentation-staleness review**

Review the final implementation diff from a fresh context. Check specifically that:

- preview never calls plan save, job-store persistence, or execution scheduling;
- normal submission still persists and executes;
- plan responses never include payload paths;
- plan IDs cannot escape the cache root;
- `--job` and positional plan lookup enforce exactly one selector;
- bearer authentication is present on preview but not required for GET lookup;
- story metadata describes the actual user journey.

Resolve valid findings, rerun focused tests, and rerun all gates after any fix.

- [ ] **Step 6: Commit UX metadata separately**

```bash
git add docs/ux_review/ops.md docs/ux_review/summary.md
git commit -m "docs(OPS-011,OPS-016): record plan preview verification"
```

- [ ] **Step 7: Confirm the final worktree and history**

Run:

```bash
git status --short
git log -8 --oneline --decorate
```

Expected: the worktree is clean, the implementation and metadata commits are visible, and no unrelated files changed.
