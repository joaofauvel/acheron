# Phase 4D Cost and Long-Term Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement all 15 Phase 4D stories as one coherent cost, recovery-administration, traceability, and job-level voice-selection program.

**Architecture:** Keep the orchestrator as the source of truth and add focused shared contracts rather than a generic operations framework. Enrich worker cost metrics first, then add store-backed recovery administration, then expose request/version traceability, and finally add validated per-chunk voice selection through the existing linear TTS stage. Memory and Redis stores must persist equivalent logical records.

**Tech Stack:** Python 3.14, FastAPI, Pydantic v2, httpx, Click, Rich, Jinja2, Redis, pytest, respx, Just, Docker Compose.

## Global Constraints

- Implement in the approved order: cost truth, recovery administration, traceability/version visibility, then voice selection.
- Use TDD for every behavior change: failing test, focused implementation, focused verification, then commit.
- Keep `ACHERON_ADMIN_TOKEN` separate from `ACHERON_REGISTRATION_TOKEN`; map the flat environment variable to `orchestrator.admin_token`, and ensure registration auth/open-registration mode never authorize `/admin/*` mutations.
- Archive hides jobs but preserves their records and artifacts; cleanup is irreversible and requires explicit apply mode.
- Cost is a rate-at-execution estimate, not invoice truth; never label RunPod's lowest-price query as an actual billed rate.
- Preserve one authoritative job contract across API, client, CLI, dashboard, memory store, and Redis store.
- Keep all public errors sanitized; do not expose credentials, internal URLs, tracebacks, or arbitrary filesystem paths. Admin failures use `{type, message, remediation}` and admin mutations emit structured audit events.
- Keep lifecycle and cost timestamps timezone-aware UTC values.
- Keep all filesystem deletion below the configured data directory and reject symlink escapes. Cleanup rechecks eligibility under the same per-job lifecycle lock used by active execution and reaping.
- Keep voice selection on one TTS worker that jointly advertises all selected voices; do not add branch-aware execution or multi-worker fan-out.
- Do not implement `MAINT-007` registration-token rotation, certificate lifecycle work, worker-image cache validation, or a generic role/JWT system.
- Use `uv` for dependency changes. No new dependency is expected for this phase.
- Run `just lint-strict`, `just type-check`, `just test`, and `just validate` at the required checkpoints; run `just ux-validate` and every Phase 4D `just ux-verify` command before completion.
- Do not update UX story metadata until its behavior and story-scoped verification pass.

---

## Dependency Graph and Stage Checkpoints

```text
Task 1: Cost domain and worker pricing
  -> Task 2: Cost propagation, persistence, and response mapping
  -> Task 3: Cost routes, client, CLI, dashboard
  -> Task 4: Cost integration and UX evidence

Task 5: Admin credential and job query contracts
  -> Task 6: Job store query/archive/delete parity
  -> Task 7: Stale-job recovery API and CLI
  -> Task 8: Cleanup, retention, and disk pressure
  -> Task 9: Worker re-registration and error history
  -> Task 10: Recovery dashboard/CLI integration and UX evidence

Task 11: Version contract and build metadata
  -> Task 12: Request-ID response/client/CLI traceability
  -> Task 13: Traceability dashboard and UX evidence

Task 14: Voice domain and strict submission contracts
  -> Task 15: Planner and dispatcher voice validation
  -> Task 16: Worker per-chunk voice resolution
  -> Task 17: Voice integration and UX evidence

Tasks 4, 10, 13, and 17
  -> Task 18: Final UX metadata, full gates, and independent review
```

Each numbered task ends with a focused test run and a Conventional Commit. Do not combine commits across stages unless a test fixture or generated contract must move with its producer.

---

## File Map

### Core contracts and worker pricing

- Modify `src/acheron/core/models.py`: add `CostBasis.STUB`, `CostEstimate`, `CostBreakdown`, voice-selection value objects, enriched `JobMetrics`, `PlanResult` cost breakdown, and request fields.
- Modify `src/acheron/core/schemas.py`: add cost response schemas, cost summaries, voice request/response schemas, and `VersionResponse`.
- Modify `src/acheron/core/planner.py`: validate and encode canonical voice selections.
- Modify `src/acheron/worker_sdk/pricing.py`: return structured measured/cached/static/stub/unknown estimates with rate metadata.
- Modify `src/acheron/worker_sdk/app.py`: choose an explicit unknown-price source instead of silently falling back to stub pricing.
- Modify `src/acheron/worker_sdk/_edge_http.py`: estimate cost for successful and failed handler executions and serialize enriched metrics.
- Modify `src/acheron/shell/transports/grpc.py`: update transport metric construction for enriched `JobMetrics`.
- Modify `src/acheron/worker_sdk/settings.py`: retain the existing explicit `price_source` contract while ensuring missing RunPod/static configuration produces unknown cost rather than stub cost.

### Persistence and orchestration

- Modify `src/acheron/shell/job_store.py`: add `JobQuery`, `archived_at`, and archive/delete/query operations.
- Modify `src/acheron/shell/stores/base.py`: define the shared job and worker-store method signatures.
- Modify `src/acheron/shell/stores/memory.py`: implement job query/archive/delete and worker error-history behavior.
- Modify `src/acheron/shell/stores/redis.py`: serialize all new job/worker fields and implement equivalent Redis operations.
- Modify `src/acheron/shell/cache.py`: expose safe plan/step-cache size and deletion operations.
- Create `src/acheron/shell/retention.py`: implement typed cleanup policy, candidate preview, path-safe deletion, and orphan-input checks.
- Modify `src/acheron/shell/orchestrator.py`: propagate cost breakdowns, list/filter jobs, mark stale jobs failed, archive jobs, preview/apply cleanup, and emit disk-pressure logs.
- Modify `src/acheron/shell/registry.py`: add `WorkerErrorEvent` history to `RegisteredWorker`.
- Modify `src/acheron/shell/health.py` and `src/acheron/shell/health_providers.py`: pass sanitized failure messages into worker-store history and preserve history through recovery.

### API, client, and CLI

- Modify `src/acheron/shell/config.py`: add `admin_token` to `OrchestratorSettings`, map flat `ACHERON_ADMIN_TOKEN` into the nested setting, and define the shared minimum-length/public-value token policy for registration and admin tokens.
- Modify `src/acheron/shell/api/deps.py`: add strict `AdminTokenDep` independent of registration auth.
- Create `src/acheron/shell/api/routes/cost.py`: expose job-cost and cost-window endpoints.
- Create `src/acheron/shell/api/routes/admin.py`: expose stale-job, archive, and cleanup administration.
- Create `src/acheron/shell/api/routes/version.py`: expose `GET /version`.
- Modify `src/acheron/shell/api/routes/jobs.py`: add typed filters, archive-aware `JobResponse.archived_at` mapping, cost mapping, sanitized source-resolution errors, and temporary-input preflight/promotion behavior.
- Modify `src/acheron/shell/api/routes/inputs.py`: add idempotent temporary-input deletion/promotion semantics for voice preflight cleanup.
- Modify `src/acheron/shell/api/routes/workers.py`: map the sanitized public current-error/history projection and omit endpoint fields from public responses.
- Modify `src/acheron/shell/api/routes/capabilities.py`: expose only an allowlisted public capability projection; never return arbitrary worker metadata, endpoints, credentials, or provider request details.
- Modify `src/acheron/shell/api/schemas.py`: add strict admin, cleanup, and voice request models.
- Modify `src/acheron/shell/api/app.py`: return `x-request-id` and register cost/admin/version routers.
- Modify `src/acheron/api_client.py`: support admin auth, cost APIs, job filters/archive, cleanup/reap, version, request-ID capture, voice fields, and temporary-input preflight cleanup.
- Modify `src/acheron/cli.py`: add cost, admin, cleanup, version, filters, request-ID output, URL diagnostics, and voice options.
- Create `src/acheron/version.py`: resolve package/build identity without reading arbitrary files.

### Dashboard and deployment metadata

- Modify `dashboard/app.py`: proxy cost summaries, version data, and worker/job filters.
- Modify `dashboard/templates/index.html`: render version identity, cost-window controls, and stuck-job controls.
- Modify `dashboard/templates/partials/jobs.html`: render archive-aware filters and stale-job metadata.
- Modify `dashboard/templates/partials/cost.html`: render aggregate totals, cost explanations, and structured basis metadata.
- Modify `dashboard/templates/partials/workers.html`: render bounded error history.
- Create `dashboard/templates/partials/version.html` if the version header uses an HTMX partial.
- Modify `Dockerfile` and `Dockerfile.edge`: pass build identity arguments into runtime environment variables.
- Modify `docker-compose.yml`: pass optional build identity arguments without introducing required deployment secrets.
- Modify `README.md` and `.env.example`: document admin-token setup, cost estimate semantics, cleanup safety, and build identity fields.

### Tests and UX evidence

- Modify `tests/core/test_models.py`, `tests/core/test_schemas.py`, and `tests/core/test_errors.py`.
- Modify `tests/worker_sdk/test_pricing.py`, `tests/worker_sdk/test_app.py`, `tests/worker_sdk/test_server.py`, and `tests/worker_sdk/test_settings.py`.
- Modify `tests/shell/test_cost.py`, `tests/shell/test_cache.py`, `tests/shell/test_orchestrator.py`, and `tests/shell/test_streaming_executor.py` for the `JobMetrics.cost_estimate` migration.
- Create `tests/shell/test_job_store.py` for typed query validation and store contract behavior.
- Modify `tests/shell/stores/test_memory_job_store.py`, `tests/shell/stores/test_redis_job_store.py`, and worker-store tests.
- Create `tests/shell/test_retention.py`.
- Modify `tests/shell/api/test_jobs.py`, `tests/shell/api/test_workers.py`, `tests/shell/api/test_capabilities.py`, and `tests/test_api_client.py`; add sanitized source-path error, archived-response, public-worker-redaction, and public-capability-projection coverage.
- Create `tests/shell/api/test_cost.py`, `tests/shell/api/test_admin.py`, and `tests/shell/api/test_version.py`.
- Modify `tests/shell/test_cli.py` and `tests/integration/test_cli_errors.py`.
- Modify `dashboard/tests/test_cost_partial.py`, `dashboard/tests/test_dashboard.py`, and `dashboard/tests/test_job_detail.py`; cover estimated-cost labels, unknown values/counts, archived rows, sanitized worker history, and version/filter behavior across cost, jobs, and detail partials.
- Modify worker handler tests under `workers/qwen3tts/tests/` and create voice planner/worker integration coverage where needed.
- Modify `tests/shell/test_grpc_worker.py`, `tests/shell/test_http_worker.py`, and relevant transport tests (`tests/shell/transports/test_multipart.py`, `test_http_multipart.py`, `test_asr_multipart.py`) for enriched `JobMetrics` construction.
- Modify `tests/integration/test_job_lifecycle.py`, `tests/integration/test_worker_registration.py`, and add recovery/voice journeys under `tests/integration/`.
- Modify `docs/ux_review/ops.md`, `docs/ux_review/maint.md`, and `docs/ux_review/summary.md` only in the final task.

---

## Stage 1 — Cost Truth

### Task 1: Define cost estimate and worker pricing contracts (`MAINT-014`, `MAINT-015`)

**Files:**
- Modify: `src/acheron/core/models.py`
- Modify: `src/acheron/core/schemas.py`
- Modify: `src/acheron/worker_sdk/pricing.py`
- Modify: `src/acheron/worker_sdk/app.py`
- Modify: `src/acheron/worker_sdk/_edge_http.py`
- Test: `tests/core/test_models.py`
- Test: `tests/core/test_schemas.py`
- Test: `tests/worker_sdk/test_pricing.py`
- Test: `tests/worker_sdk/test_app.py`

**Interfaces:**
- Consumes: current `CostBasis`, `PriceEstimate`, `PriceSource`, `JobMetrics`, and `JobResult` contracts.
- Produces: `CostBasis.STUB`, `CostEstimate`, `CostBreakdown`, enriched `PriceEstimate`, and `PriceSource.estimate()` results with rate metadata.

- [ ] **Step 1: Write failing tests for cost basis and metadata.**

Add tests for the five explicit basis states and metadata round-tripping:

```python
from datetime import UTC, datetime

from acheron.core.models import CostBasis, CostEstimate


def test_stub_cost_is_not_static() -> None:
    estimate = CostEstimate(cost=0.0, basis=CostBasis.STUB)

    assert estimate.basis is CostBasis.STUB


def test_cost_estimate_preserves_rate_for_forensics() -> None:
    queried_at = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
    estimate = CostEstimate(
        cost=0.34,
        basis=CostBasis.MEASURED,
        rate_per_hour=0.69,
        gpu_type="L4",
        secure_cloud=False,
        queried_at=queried_at,
        cache_age_seconds=0.0,
    )

    assert estimate.gpu_type == "L4"
    assert estimate.secure_cloud is False
    assert estimate.queried_at == queried_at
```

Add worker pricing tests proving `ZeroPrice` produces `STUB`, `StaticPrice` produces `STATIC`, a failed refresh with a warm cache produces `CACHED` with cache age, and a missing initial rate produces `UNKNOWN` rather than `STUB`.

- [ ] **Step 2: Run the focused tests and verify failure.**

Run:

```bash
uv run pytest --no-cov tests/core/test_models.py tests/core/test_schemas.py tests/worker_sdk/test_pricing.py tests/worker_sdk/test_app.py -q
```

Expected: failures for the missing `STUB` basis, structured estimate fields, and unknown-price behavior.

- [ ] **Step 3: Implement the core cost types.**

In `src/acheron/core/models.py`, define the value objects without optional basis ambiguity:

```python
@dataclass(frozen=True)
class CostEstimate:
    cost: float | None
    basis: CostBasis
    rate_per_hour: float | None = None
    gpu_type: str | None = None
    secure_cloud: bool | None = None
    queried_at: datetime | None = None
    cache_age_seconds: float | None = None


@dataclass(frozen=True)
class CostBreakdown:
    step_id: str
    worker_type: WorkerType
    worker_id: str | None
    gpu_seconds: float | None
    estimate: CostEstimate
```

Change `JobMetrics` to use `cost_estimate: CostEstimate | None` and add `cost_breakdown` only at the plan-result layer, where step identity is available. Add `CostBasis.STUB`.

In `src/acheron/core/schemas.py`, add matching Pydantic response models with UTC validation for `queried_at` and `cache_age_seconds >= 0` validation.

- [ ] **Step 4: Replace reason-string dispatch in the pricing sources.**

Update `PriceEstimate` to carry `basis`, `rate_per_hour`, `gpu_type`, `secure_cloud`, `queried_at`, and `cache_age_seconds`. Make `ZeroPrice` return `basis=STUB`; make `StaticPrice` return `basis=STATIC`; make RunPod return `MEASURED`, `CACHED`, or `UNKNOWN` directly.

Track both monotonic freshness and wall-clock query time in `RunPodPrice`. On refresh, retain the provider GPU identifier as `gpu_type`, the configured `secure_cloud`, the rate, and the UTC query time. On a stale-cache refresh failure, return the cached rate with the computed wall-clock age. When no rate exists, return `cost=None` and `basis=UNKNOWN`.

Add an explicit unknown-price implementation for missing RunPod credentials and for invalid static configuration. Do not route either case through `ZeroPrice`.

- [ ] **Step 5: Estimate failed and successful worker executions.**

In `_edge_http.py`, call the price source after both successful handler completion and handler failure. Failed results must retain duration and cost metadata when a price is available. Pricing is non-blocking: if the price source or basis conversion raises unexpectedly, chain the exception into a sanitized warning and emit `CostEstimate(cost=None, basis=CostBasis.UNKNOWN)` rather than failing the worker result. Use `cost_estimate=None` only when metrics cannot be produced at all. Add a test for an unexpected pricing-source exception.

- [ ] **Step 6: Run focused tests and commit.**

Run:

```bash
uv run pytest --no-cov tests/core/test_models.py tests/core/test_schemas.py tests/worker_sdk/test_pricing.py tests/worker_sdk/test_app.py -q
```

Expected: PASS. Commit:

```bash
git add src/acheron/core/models.py src/acheron/core/schemas.py src/acheron/worker_sdk/pricing.py src/acheron/worker_sdk/app.py src/acheron/worker_sdk/_edge_http.py tests/core/test_models.py tests/core/test_schemas.py tests/worker_sdk/test_pricing.py tests/worker_sdk/test_app.py
git commit -m "feat(MAINT-014,MAINT-015): define truthful cost estimates"
```

### Task 2: Propagate cost breakdowns through execution and persistence (`MAINT-002`)

**Files:**
- Modify: `src/acheron/core/models.py`
- Modify: `src/acheron/shell/executors/sequential.py`
- Modify: `src/acheron/shell/executors/async_executor.py`
- Modify: `src/acheron/shell/executors/streaming.py`
- Modify: `src/acheron/shell/orchestrator.py`
- Modify: `src/acheron/shell/job_store.py`
- Modify: `src/acheron/shell/stores/memory.py`
- Modify: `src/acheron/shell/stores/redis.py`
- Modify: `src/acheron/shell/transports/grpc.py`
- Modify: `tests/shell/test_cost.py`
- Modify: `tests/shell/test_orchestrator.py`
- Modify: `tests/shell/test_streaming_executor.py`
- Modify: `tests/shell/test_executors.py`
- Modify: `tests/shell/test_http_worker.py`
- Modify: `tests/shell/test_grpc_worker.py`
- Modify: `tests/shell/transports/test_multipart.py`
- Modify: `tests/shell/transports/test_http_multipart.py`
- Modify: `tests/shell/transports/test_asr_multipart.py`
- Modify: `tests/shell/stores/test_memory_job_store.py`
- Modify: `tests/shell/stores/test_redis_job_store.py`

**Interfaces:**
- Consumes: `CostEstimate`, `CostBreakdown`, enriched `JobMetrics`, and `JobResult.worker_id` from Task 1.
- Produces: `build_cost_breakdown(step: PlanStep, result: JobResult) -> CostBreakdown | None`, `PlanResult.cost_breakdown`, persisted per-step cost metadata, and stable aggregate cost mapping for `JobResponse`.

- [ ] **Step 1: Write failing executor and store round-trip tests.**

Create a successful and failed `JobResult` carrying a structured estimate, then assert the plan result retains step and worker identity. Add a cache-hit fixture with no pricing attempt and assert it contributes no breakdown item, plus an attempted-but-unpriced fixture with `CostEstimate(cost=None, basis=CostBasis.UNKNOWN)` and assert it contributes one `UNKNOWN` breakdown item:

```python
def test_plan_result_keeps_cost_breakdown_for_failed_step() -> None:
    estimate = CostEstimate(
        cost=0.34,
        basis=CostBasis.MEASURED,
        rate_per_hour=0.69,
        gpu_type="L4",
        secure_cloud=False,
        queried_at=datetime(2026, 7, 30, tzinfo=UTC),
        cache_age_seconds=0.0,
    )
    result = JobResult(
        job_id="job-1-step",
        status=JobStatus.FAILED,
        outputs=(),
        metrics=JobMetrics(duration_seconds=1800.0, gpu_seconds=1800.0, cost_estimate=estimate),
        error="worker failed",
        worker_id="tts-1",
    )

    breakdown = build_cost_breakdown(
        PlanStep("synthesize", WorkerType.TTS, (), StepStatus.PENDING, {}),
        result,
    )

    assert breakdown is not None
    assert breakdown.worker_id == "tts-1"
    assert breakdown.estimate.gpu_type == "L4"
```

Add memory and Redis serialization tests that assert `CostEstimate` fields survive `put()` and `get()` for both completed and failed jobs.

- [ ] **Step 2: Run focused tests and verify failure.**

Run:

```bash
uv run pytest --no-cov tests/shell/test_cost.py tests/shell/test_orchestrator.py tests/shell/test_streaming_executor.py tests/shell/test_executors.py tests/shell/test_http_worker.py tests/shell/test_grpc_worker.py tests/shell/transports/test_multipart.py tests/shell/transports/test_http_multipart.py tests/shell/transports/test_asr_multipart.py tests/shell/stores/test_memory_job_store.py tests/shell/stores/test_redis_job_store.py -q
```

Expected: failures because executors, transports, and stores discard or cannot serialize structured per-step cost data.

- [ ] **Step 3: Add cost breakdown accumulation.**

Define the shared mapper in `src/acheron/shell/cost.py`:

```python
def build_cost_breakdown(step: PlanStep, result: JobResult) -> CostBreakdown | None:
    estimate = result.metrics.cost_estimate
    if estimate is None:
        # Cache hits without a pricing attempt intentionally have no item.
        return None
    return CostBreakdown(
        step_id=step.step_id,
        worker_type=step.type,
        worker_id=result.worker_id,
        gpu_seconds=result.metrics.gpu_seconds,
        estimate=estimate,
    )
```

Keep `gpu_seconds` on `JobMetrics` and copy it into `CostBreakdown`; do not duplicate it on `CostEstimate`. Add `cost_breakdown: tuple[CostBreakdown, ...] = ()` to `PlanResult`. Executors append a breakdown item for every attempted execution, including failed results. An attempted execution with no usable rate carries `CostEstimate(cost=None, basis=CostBasis.UNKNOWN)` and therefore produces an explicit `UNKNOWN` item; a cache hit that did not attempt pricing carries no estimate and produces no item. Keep aggregate `total_cost` calculated only from non-`None` estimate costs.

Update `aggregate_cost_basis` to consume the estimates represented in `CostBreakdown` and return the least-confidence basis across the executed steps using this explicit order: `MEASURED` > `CACHED` > `STATIC` > `STUB` > `UNKNOWN`. `UNKNOWN` items count toward unknown-job reporting but never contribute zero dollars; omitted cache-hit items do not affect the aggregate basis. Add mixed-basis tests covering every adjacent and extreme pair, including `STUB` with `UNKNOWN`.

Update orchestrator progress recording so `CostBreakdown.worker_id` comes from the dispatch result and `worker_type`/`step_id` come from the plan step.

- [ ] **Step 4: Serialize the new result data in both stores.**

Extend memory cloning and Redis JSON serialization/deserialization for:

```text
PlanResult.cost_breakdown[]
  step_id
  worker_type
  worker_id
  gpu_seconds
  estimate.cost
  estimate.basis
  estimate.rate_per_hour
  estimate.gpu_type
  estimate.secure_cloud
  estimate.queried_at
  estimate.cache_age_seconds
```

Reject malformed enum values, naive timestamps, negative cache ages, and non-finite numeric values with the existing `CacheCorruptedError` chain.

- [ ] **Step 5: Run cost and persistence tests and commit.**

Run:

```bash
uv run pytest --no-cov tests/shell/test_cost.py tests/shell/test_orchestrator.py tests/shell/test_streaming_executor.py tests/shell/test_executors.py tests/shell/test_http_worker.py tests/shell/test_grpc_worker.py tests/shell/transports/test_multipart.py tests/shell/transports/test_http_multipart.py tests/shell/transports/test_asr_multipart.py tests/shell/stores/test_memory_job_store.py tests/shell/stores/test_redis_job_store.py -q
```

Expected: PASS. Commit:

```bash
git add src/acheron/core/models.py src/acheron/shell/executors src/acheron/shell/orchestrator.py src/acheron/shell/job_store.py src/acheron/shell/stores src/acheron/shell/transports/grpc.py tests/shell/test_cost.py tests/shell/test_orchestrator.py tests/shell/test_streaming_executor.py tests/shell/test_grpc_worker.py tests/shell/stores
git commit -m "feat(MAINT-002): persist per-step cost evidence"
```

### Task 3: Expose cost APIs, CLI explanation, and dashboard reporting (`OPS-005`, `OPS-031`, `MAINT-002`)

**Files:**
- Modify: `src/acheron/core/schemas.py`
- Create: `src/acheron/shell/api/routes/cost.py`
- Modify: `src/acheron/shell/api/routes/jobs.py`
- Modify: `src/acheron/shell/api/app.py`
- Modify: `src/acheron/api_client.py`
- Modify: `src/acheron/cli.py`
- Modify: `dashboard/app.py`
- Modify: `dashboard/templates/index.html`
- Modify: `dashboard/templates/partials/cost.html`
- Test: `tests/shell/api/test_cost.py`
- Modify: `tests/shell/api/test_jobs.py`
- Modify: `tests/test_api_client.py`
- Modify: `tests/shell/test_cli.py`
- Modify: `dashboard/tests/test_cost_partial.py`
- Modify: `dashboard/tests/test_dashboard.py`

**Interfaces:**
- Consumes: persisted `PlanResult.cost_breakdown` and `JobResponse` mapping from Task 2.
- Produces: `JobCostResponse`, `CostSummaryResponse`, `JobResponse.archived_at` mapping, `AcheronClient.get_job_cost()`, `AcheronClient.get_cost_summary()`, `acheron job cost ID --explain`, and cost-window dashboard rendering.

- [ ] **Step 1: Write failing API/client/CLI/dashboard tests.**

Add a route test for a measured failed job and a submission-error test proving missing/unreadable source paths return sanitized public messages without absolute filesystem paths while retaining detailed paths only in internal logs:

```python
async def test_get_job_cost_exposes_gpu_and_cache_age(client, job_factory) -> None:
    job = job_factory(
        total_cost=0.34,
        cost_breakdown=[
            {
                "step_id": "synthesize",
                "worker_type": "tts",
                "worker_id": "tts-1",
                "gpu_seconds": 1800.0,
                "cost": 0.34,
                "basis": "measured",
                "rate_per_hour": 0.69,
                "gpu_type": "L4",
                "secure_cloud": False,
                "queried_at": "2026-07-30T12:00:00Z",
                "cache_age_seconds": 0.0,
            }
        ],
    )

    response = await client.get(f"/jobs/{job.job_id}/cost")

    assert response.status_code == 200
    assert response.json()["cost_breakdown"][0]["gpu_type"] == "L4"
```

Add client tests for `/jobs/{id}/cost` and an actual `GET /cost?window=7d` request. Add an API test that proves `window` is read from the query string rather than a request body. Add CLI tests that `job cost --explain`, status, and job-detail output label values as execution-time estimates and render unknown values/counts without `$0.00` or a free-usage implication. Add template tests for the four window controls, aggregate footer, unknown count, estimated-cost label, and basis explanation text.

- [ ] **Step 2: Run focused tests and verify failure.**

Run:

```bash
uv run pytest --no-cov tests/shell/api/test_cost.py tests/shell/api/test_jobs.py tests/test_api_client.py tests/shell/test_cli.py dashboard/tests/test_cost_partial.py dashboard/tests/test_dashboard.py dashboard/tests/test_job_detail.py -q
```

Expected: failures because the cost router, client methods, CLI command, and dashboard data flow do not exist.

- [ ] **Step 3: Define cost response schemas and aggregation.**

Add response models:

```python
class CostBreakdownResponse(BaseModel):
    step_id: str
    worker_type: WorkerType
    worker_id: str | None
    gpu_seconds: float | None
    cost: float | None
    basis: CostBasis
    rate_per_hour: float | None
    gpu_type: str | None
    secure_cloud: bool | None
    queried_at: datetime | None
    cache_age_seconds: float | None


class JobCostResponse(BaseModel):
    job_id: str
    total_cost: float
    total_cost_basis: CostBasis | None
    cost_breakdown: list[CostBreakdownResponse]


class CostSummaryResponse(BaseModel):
    window: str
    since: datetime | None
    until: datetime
    total_cost: float
    job_count: int
    unknown_cost_jobs: int


class CostWindowQuery(BaseModel):
    window: Literal["24h", "7d", "30d", "all"] = "7d"
```

Bind `CostWindowQuery` exactly as `Annotated[CostWindowQuery, Query()]`; do not accept a request body for `GET /cost`.

Implement `Orchestrator.get_job_cost(job_id)` by loading the tracked job and mapping its persisted result. Implement `Orchestrator.get_cost_summary(window)` by querying terminal/non-archived jobs in the requested window and counting unknown estimates without treating them as zero.

- [ ] **Step 4: Add cost routes and client methods.**

Create `src/acheron/shell/api/routes/cost.py` with:

```python
@router.get("/jobs/{job_id}/cost", response_model=JobCostResponse)
async def get_job_cost(job_id: str, orch: OrchestratorDep) -> JobCostResponse: ...

@router.get("/cost", response_model=CostSummaryResponse)
async def get_cost_summary(
    window: Annotated[CostWindowQuery, Query()],
    orch: OrchestratorDep,
) -> CostSummaryResponse: ...
```

Import `Annotated`, `Literal`, and `Query`; register the router at the root so the paths are exactly `/jobs/{id}/cost` and `/cost`. Add client methods that call these read-only endpoints and validate the response models.

- [ ] **Step 5: Add CLI and dashboard rendering.**

Add `job cost` with `--explain`. Render one summary block and one row per cost breakdown item. Unknown values render as `unknown`, not `$0.00`, and every total in job cost, status, job detail, jobs rows, and dashboard partials is labeled as an execution-time estimate rather than an invoice amount. Display unknown-job counts wherever aggregate totals appear.

Update the dashboard cost partial to fetch the selected summary window and render:

```html
<tfoot>
  <tr><td colspan="7">Estimated cost, last 7d: $X.XX (N jobs; U unknown)</td></tr>
</tfoot>
```

Render a basis tooltip for measured, cached, unknown, static, and stub. Include GPU type, secure-cloud mode, rate, query timestamp, cache age, and unknown-cost count where present. Add tests asserting the visible `Estimated cost` label, unknown count, and non-invoice tooltip text.

- [ ] **Step 6: Run focused tests and commit.**

Run:

```bash
uv run pytest --no-cov tests/shell/api/test_cost.py tests/shell/api/test_jobs.py tests/test_api_client.py tests/shell/test_cli.py dashboard/tests/test_cost_partial.py dashboard/tests/test_dashboard.py dashboard/tests/test_job_detail.py -q
```

Expected: PASS. Commit:

```bash
git add src/acheron/core/schemas.py src/acheron/shell/api/routes/cost.py src/acheron/shell/api/routes/jobs.py src/acheron/shell/api/app.py src/acheron/api_client.py src/acheron/cli.py dashboard/app.py dashboard/templates/index.html dashboard/templates/partials/cost.html tests/shell/api/test_cost.py tests/shell/api/test_jobs.py tests/test_api_client.py tests/shell/test_cli.py dashboard/tests/test_cost_partial.py dashboard/tests/test_dashboard.py
git commit -m "feat(OPS-005,OPS-031): expose cost explanations and windows"
```

### Task 4: Verify cost truth through simulations and UX evidence (`MAINT-014`, `MAINT-015`, `OPS-005`, `OPS-031`, `MAINT-002`)

**Files:**
- Modify: `sim/scenarios/pricing_outage.py`
- Modify: `sim/scenarios/gpu_switch.py`
- Modify: `sim/scenarios/INDEX.md`
- Modify: `tests/integration/test_job_lifecycle.py`
- Modify: `docs/ux_review/ops.md`
- Modify: `docs/ux_review/maint.md`
- Modify: `docs/ux_review/summary.md`

**Interfaces:**
- Consumes: cost APIs, worker pricing metadata, and dashboard/CLI output from Tasks 1–3.
- Produces: deterministic pricing-outage/GPU-switch evidence and story-scoped verification artifacts.

- [ ] **Step 1: Add deterministic pricing-outage assertions.**

Update the existing pricing outage scenario so it asserts:

- a warm cache yields `CACHED` with positive cache age;
- no warm cache yields `UNKNOWN`;
- neither case yields `STUB` unless the worker is explicitly configured with `price_source=zero`.

- [ ] **Step 2: Add deterministic GPU-switch assertions.**

Update the GPU-switch scenario so a refreshed endpoint identity changes the recorded `gpu_type` and rate metadata, while an outage retains the prior cached identity and marks the estimate `CACHED`.

- [ ] **Step 3: Add failed-job cost integration coverage.**

Force a worker failure after measurable duration and assert the persisted `JobResponse.cost_breakdown` contains GPU seconds, rate basis, GPU identity, and cache age when available.

- [ ] **Step 4: Run story-scoped verification.**

Run:

```bash
uv run pytest --no-cov tests/worker_sdk/test_pricing.py tests/worker_sdk/test_runpod_price.py tests/integration/test_job_lifecycle.py -q
just sim-run pricing_outage
just sim-run gpu_switch
just ux-verify MAINT-014
just ux-verify MAINT-015
just ux-verify OPS-005
just ux-verify OPS-031
just ux-verify MAINT-002
```

Expected: no command returns `FAIL`; stories without harness artifacts may return `PARTIAL` before metadata exists. After the behavior and simulation evidence pass, update their `fixed_in`, `verified_in`, `last_verified_at`, and `verified_by` metadata, then rerun all five commands and require `PASS`. Commit:

```bash
git add sim/scenarios tests/integration/test_job_lifecycle.py docs/ux_review/ops.md docs/ux_review/maint.md docs/ux_review/summary.md
git commit -m "test(phase-4d): verify cost truth journeys"
```

### Stage 1 checkpoint

- [ ] `CostBasis.STUB` is distinct from `STATIC`.
- [ ] Failed worker executions retain available cost evidence.
- [ ] Memory and Redis job records round-trip cost breakdowns.
- [ ] `/jobs/{id}/cost`, `/cost`, CLI explanation, and dashboard windows pass focused tests.
- [ ] All five Stage 1 `just ux-verify` commands pass.

---

## Stage 2 — Recovery Administration

### Task 5: Add admin credentials and typed job-query contracts (`MAINT-001`, `OPS-012`)

**Files:**
- Modify: `src/acheron/shell/config.py`
- Modify: `src/acheron/shell/orchestrator.py`
- Modify: `src/acheron/shell/api/deps.py`
- Modify: `src/acheron/shell/job_store.py`
- Modify: `src/acheron/shell/api/schemas.py`
- Test: `tests/shell/test_config.py`
- Modify: `tests/shell/api/test_jobs.py`
- Modify: `tests/shell/api/test_app.py`
- Create: `tests/shell/api/test_admin.py`

**Interfaces:**
- Consumes: existing `OrchestratorSettings`, `RegistrationTokenDep`, `TrackedJob`, and `PlanStatus`.
- Produces: `OrchestratorSettings.admin_token`, `AdminTokenDep`, `JobQuery`, strict cleanup/reap request models, and structured admin auth failures.

- [ ] **Step 1: Write failing admin-auth and query tests.**

Add tests for all authorization states:

```python
async def test_open_registration_does_not_authorize_admin(client, settings) -> None:
    settings.orchestrator.open_registration = True
    settings.orchestrator.admin_token = None

    response = await client.post(
        "/admin/jobs/reap-stale",
        json={"older_than_seconds": 60, "reason": "restart"},
    )

    assert response.status_code == 503


async def test_admin_route_rejects_registration_token(client, settings) -> None:
    settings.orchestrator.admin_token = "a" * 32
    settings.orchestrator.registration_token = "r" * 32

    response = await client.post(
        "/admin/jobs/reap-stale",
        json={"older_than_seconds": 60, "reason": "restart"},
        headers={"Authorization": "Bearer " + settings.orchestrator.registration_token},
    )

    assert response.status_code == 401
```

Add `JobQuery` validation tests for status, UTC windows, stale age, and archive inclusion. The store `list()` method accepts an optional keyword-only `now` value so stale-age tests are deterministic without embedding a clock in the query object.

- [ ] **Step 2: Run focused tests and verify failure.**

Run:

```bash
uv run pytest --no-cov tests/shell/test_config.py tests/shell/api/test_jobs.py tests/shell/api/test_app.py tests/shell/api/test_admin.py -q
```

Expected: failures for the missing setting, dependency, query contract, app-level admin error/audit seam, and routes.

- [ ] **Step 3: Add the admin token setting and dependency.**

Add `admin_token: str | None = None` to `OrchestratorSettings`. Define an explicit shared token policy used by both registration and admin settings: reject values shorter than 32 characters and known public/example values, while allowing absent values where the feature is optional. Extend `_EnvAliasSettingsSource` so flat `ACHERON_ADMIN_TOKEN` maps to `orchestrator.admin_token`, while structured `ACHERON_ORCHESTRATOR__ADMIN_TOKEN` retains precedence. Add settings tests for flat loading, structured override, minimum length, public-value rejection, and absent-token read-only startup; do not require an admin token at read-only startup.

Add:

```python
AdminTokenDep = Annotated[None, Depends(verify_admin_token)]
```

`verify_admin_token` must return `503` when the setting is absent, `401` for missing/invalid credentials, and use `secrets.compare_digest` for comparison. It must not call `verify_registration_token` or `has_registration_token`.

Define one shared `AdminErrorResponse` with `{type: str, message: str, remediation: str | None}` and use it for unavailable configuration, invalid credentials, missing jobs, conflicts, archive failures, reap failures, and cleanup failures. Add tests asserting this body for `503`, `401`, `404`, and `409` cases.

Define one `AdminActionAudit` event with `request_id`, `action`, `reason: str | None`, `job_ids: tuple[str, ...]`, `affected_count`, and `result: Literal["success", "failure"]`. Add an admin-router dependency/exception-handler seam that catches failed authorization, request validation, and route exceptions before route execution, derives the action from the matched admin path, records exactly one failure event, and returns `AdminErrorResponse`; route success/failure logging must suppress duplicate events. Add success/failure assertions for archive, mark-failed, reap, and cleanup, including missing/invalid body fields.

- [ ] **Step 4: Define typed job queries and request models.**

Add:

```python
@dataclass(frozen=True)
class JobQuery:
    status: PlanStatus | None = None
    since: datetime | None = None
    before: datetime | None = None
    older_than_seconds: float | None = None
    include_archived: bool = False
```

Add strict JSON-body API models for stale reaping, mark-failed, archive, and cleanup. Use canonical body fields (`older_than_seconds`, `reason`, and `apply`/retention fields); do not mix query-parameter and body forms for admin mutations. Parse durations at the API boundary into finite non-negative seconds; reject zero/negative retention windows and naive timestamps.

- [ ] **Step 5: Run focused tests and commit.**

Run:

```bash
uv run pytest --no-cov tests/shell/test_config.py tests/shell/api/test_jobs.py tests/shell/api/test_app.py tests/shell/api/test_admin.py -q
```

Expected: PASS. Commit:

```bash
git add src/acheron/shell/config.py src/acheron/shell/orchestrator.py src/acheron/shell/api/deps.py src/acheron/shell/job_store.py src/acheron/shell/api/schemas.py tests/shell/test_config.py tests/shell/api/test_jobs.py tests/shell/api/test_admin.py
git commit -m "feat(MAINT-001): add separate admin authorization"
```

### Task 6: Implement memory/Redis job filtering, archive, and deletion (`OPS-012`)

**Files:**
- Modify: `src/acheron/shell/job_store.py`
- Modify: `src/acheron/shell/stores/base.py`
- Modify: `src/acheron/shell/stores/memory.py`
- Modify: `src/acheron/shell/stores/redis.py`
- Modify: `src/acheron/shell/api/routes/jobs.py`
- Modify: `tests/shell/api/test_jobs.py`
- Modify: `src/acheron/shell/orchestrator.py`
- Modify: `src/acheron/core/schemas.py`
- Modify: `tests/shell/api/test_jobs.py`
- Create: `tests/shell/test_job_store.py`
- Modify: `tests/shell/stores/test_memory_job_store.py`
- Modify: `tests/shell/stores/test_redis_job_store.py`

**Interfaces:**
- Consumes: `JobQuery` and `TrackedJob.archived_at` from Task 5.
- Produces: `JobStore.list(query)`, `JobStore.archive(job_id)`, `JobStore.delete(job_id)`, and equivalent `Orchestrator.list_jobs(query)` behavior.

- [ ] **Step 1: Write failing parity tests.**

Seed completed, failed, running, and archived jobs with distinct `created_at` values. Assert:

```python
jobs = await store.list(
    JobQuery(status=PlanStatus.RUNNING, older_than_seconds=1800),
    now=now,
)
assert [job.job_id for job in jobs] == ["job-stuck"]

archived = await store.archive("job-completed")
assert archived.archived_at is not None
assert [job.job_id for job in await store.list(JobQuery())] == ["job-stuck"]
assert [job.job_id for job in await store.list(JobQuery(include_archived=True))] == ["job-completed", "job-stuck"]
```

Use the same parameterized behavior test for memory and Redis stores. Add an API mapping test that archives a job, fetches it with `include_archived=true`, and asserts `archived_at` is present while the job record, plan, outputs, inputs, and cost breakdown remain unchanged. Add the same preservation assertion to the archive route idempotency tests.

- [ ] **Step 2: Run focused tests and verify failure.**

Run:

```bash
uv run pytest --no-cov tests/shell/test_job_store.py tests/shell/stores/test_memory_job_store.py tests/shell/stores/test_redis_job_store.py tests/shell/api/test_jobs.py -q
```

Expected: failures for missing query/archive/delete methods, Redis fields, and archived response mapping.

- [ ] **Step 3: Extend the store protocols and tracked job model.**

Add `archived_at: datetime | None = None` to `TrackedJob` and normalize it to UTC when present. Add `archived_at: datetime | None` to `JobResponse` and map it in `_tracked_to_response()` so archived list/dashboard rows can render state. Replace direct `list_all()` calls in the orchestrator and routes with `list(JobQuery(...))`.

Define store methods:

```python
async def list(
    self,
    query: JobQuery = JobQuery(),
    *,
    now: datetime | None = None,
) -> tuple[TrackedJob, ...]: ...
async def archive(self, job_id: str, *, archived_at: datetime | None = None) -> TrackedJob: ...
async def delete(self, job_id: str) -> TrackedJob | None: ...
```

Filtering must happen in the store for Redis-backed deployments; do not load every job into the API route solely to filter it.

- [ ] **Step 4: Implement memory and Redis behavior.**

Memory filtering compares normalized UTC timestamps and excludes archived jobs unless requested. Redis stores `archived_at` in the serialized job blob, filters after deterministic sorted retrieval, and atomically removes the job key from both the job set and job record during deletion.

Archive is idempotent and updates `last_persisted_at`. Delete returns the removed record so the retention service can use its source and plan references.

- [ ] **Step 5: Run store tests and commit.**

Run:

```bash
uv run pytest --no-cov tests/shell/test_job_store.py tests/shell/stores/test_memory_job_store.py tests/shell/stores/test_redis_job_store.py tests/shell/api/test_jobs.py -q
```

Expected: PASS. Commit:

```bash
git add src/acheron/shell/job_store.py src/acheron/shell/stores/base.py src/acheron/shell/stores/memory.py src/acheron/shell/stores/redis.py src/acheron/shell/orchestrator.py src/acheron/core/schemas.py tests/shell/api/test_jobs.py tests/shell/test_job_store.py tests/shell/stores
git commit -m "feat(OPS-012): add queryable archived job records"
```

### Task 7: Add stale-job marking, reaping, and CLI administration (`MAINT-001`)

**Files:**
- Create: `src/acheron/shell/api/routes/admin.py`
- Modify: `src/acheron/shell/api/app.py`
- Modify: `src/acheron/shell/orchestrator.py`
- Modify: `src/acheron/api_client.py`
- Modify: `src/acheron/cli.py`
- Modify: `src/acheron/core/schemas.py`
- Modify: `tests/shell/api/test_admin.py`
- Modify: `tests/shell/test_orchestrator.py`
- Modify: `tests/test_api_client.py`
- Modify: `tests/shell/test_cli.py`
- Modify: `tests/integration/test_job_lifecycle.py`

**Interfaces:**
- Consumes: `AdminTokenDep`, `JobQuery`, store archive/delete/query methods, `StepError`, and the orchestrator’s `_active_jobs` set.
- Produces: `ReapResult`, `Orchestrator.mark_failed_by_admin()`, `Orchestrator.reap_stale_jobs()`, `Orchestrator.archive_job()`, `ReapStaleResponse`, `AdminJobResponse`, `AcheronClient.reap_stale_jobs()`, `AcheronClient.mark_job_failed()`, `AcheronClient.archive_job()`, and `acheron admin reap-stuck`/`acheron job archive`.

- [ ] **Step 1: Write failing stale-job tests.**

Cover a job that is persisted `RUNNING` but absent from `_active_jobs`, a genuinely active job, and a terminal job:

```python
reaped = await orchestrator.reap_stale_jobs(older_than_seconds=60, reason="orphaned_by_restart", now=now)

assert reaped.job_ids == ("job-orphaned",)
assert (await job_store.get("job-orphaned")).status is PlanStatus.FAILED
assert (await job_store.get("job-orphaned")).result.errors[-1].message == "orphaned_by_restart"
assert (await job_store.get("job-active")).status is PlanStatus.RUNNING
```

Add API tests for admin auth, `200` count/IDs, required JSON-body reason, structured `404`/`409` errors, exactly-one failure audit events, and sanitized source-path errors on public job submission routes.

- [ ] **Step 2: Run focused tests and verify failure.**

Run:

```bash
uv run pytest --no-cov tests/shell/api/test_admin.py tests/shell/test_orchestrator.py tests/test_api_client.py tests/shell/test_cli.py tests/integration/test_job_lifecycle.py -q
```

Expected: failures because the admin router, orchestrator methods, client methods, and CLI group do not exist.

- [ ] **Step 3: Implement orchestrator stale-job transitions.**

Define the internal result and add methods with deterministic UTC cutoffs:

```python
@dataclass(frozen=True)
class ReapResult:
    job_ids: tuple[str, ...]


async def mark_failed_by_admin(self, job_id: str, *, reason: str) -> TrackedJob: ...

async def reap_stale_jobs(
    self,
    *,
    older_than_seconds: float,
    reason: str,
    now: datetime | None = None,
) -> ReapResult: ...
```

Use a per-job lifecycle lock. Refuse terminal jobs and refuse jobs in `_active_jobs`. For eligible jobs, set `status=FAILED`, preserve outputs/progress/cost, append a sanitized job-level `StepError`, persist, and publish a terminal event. The reason is trimmed and bounded before entering public state.

- [ ] **Step 4: Implement admin routes and client auth.**

The admin router uses `AdminTokenDep` on every mutation. Add response models:

```python
class ReapStaleResponse(BaseModel):
    reaped: int
    job_ids: list[str]

class AdminJobResponse(BaseModel):
    job: JobResponse
```

Use `/admin/jobs/{job_id}/mark-failed` and `/admin/jobs/reap-stale` with the strict JSON-body models from Task 5. Do not place admin routes under `/jobs`, so registration-token mutation dependencies cannot accidentally apply. Use the shared `AdminErrorResponse` for every failure and emit the `AdminActionAudit` event for both successful and failed requests.

Add `POST /admin/jobs/{job_id}/archive` with `AdminTokenDep`, `AdminJobResponse`, idempotent success for an already archived job, and structured `404`/`409` failures. Add `AcheronClient.archive_job()` using a separate `_admin_headers()` method; the CLI archive command must call this route and never mutate the store directly. Add API/client/CLI tests for valid admin auth, registration-token rejection, missing job, repeated archive, and audit events.

Extend `AcheronClient` construction with `admin_token: str | None`, and update CLI `_get_client()` to read `ACHERON_ADMIN_TOKEN` separately from `ACHERON_REGISTRATION_TOKEN`. Add a separate `_admin_headers()` method and admin methods that never reuse `_mutation_headers()`; tests must prove each token is sent only to its intended mutation surface.

- [ ] **Step 5: Add the CLI admin group.**

The archive command uses `AcheronClient.archive_job()` for each ID, renders the structured response, and reports `ACHERON_ADMIN_TOKEN` remediation before making a request when the token is absent.

Add:

```python
@main.group()
def admin() -> None:
    """Perform operator-only recovery actions."""

@admin.command("reap-stuck")
@click.option("--older-than", required=True)
@click.option("--reason", required=True)
def reap_stuck(older_than: str, reason: str) -> None: ...
```

Render `reaped=N` and each job ID. Missing `ACHERON_ADMIN_TOKEN` must produce an actionable error without making a request.

- [ ] **Step 6: Run focused tests and commit.**

Run:

```bash
uv run pytest --no-cov tests/shell/api/test_admin.py tests/shell/test_orchestrator.py tests/test_api_client.py tests/shell/test_cli.py tests/integration/test_job_lifecycle.py -q
```

Expected: PASS. Commit:

```bash
git add src/acheron/shell/api/routes/admin.py src/acheron/shell/api/app.py src/acheron/shell/orchestrator.py src/acheron/api_client.py src/acheron/cli.py src/acheron/core/schemas.py tests/shell/api/test_admin.py tests/shell/test_orchestrator.py tests/test_api_client.py tests/shell/test_cli.py tests/integration/test_job_lifecycle.py
git commit -m "feat(MAINT-001): reap orphaned jobs safely"
```

### Task 8: Implement retention cleanup, archive command, and disk-pressure checks (`MAINT-012`, `OPS-012`)

**Files:**
- Create: `src/acheron/shell/retention.py`
- Modify: `src/acheron/shell/cache.py`
- Modify: `src/acheron/shell/input_store.py`
- Modify: `src/acheron/shell/api/routes/jobs.py`
- Modify: `src/acheron/shell/orchestrator.py`
- Modify: `src/acheron/shell/api/routes/admin.py`
- Modify: `src/acheron/api_client.py`
- Modify: `src/acheron/cli.py`
- Modify: `src/acheron/shell/api/schemas.py`
- Create: `tests/shell/test_retention.py`
- Modify: `tests/shell/test_cache.py`
- Modify: `tests/shell/test_orchestrator.py`
- Modify: `tests/shell/test_input_store.py`
- Modify: `tests/shell/api/test_jobs.py`
- Modify: `tests/shell/api/test_admin.py`
- Modify: `tests/test_api_client.py`
- Modify: `tests/shell/test_cli.py`

**Interfaces:**
- Consumes: store query/archive/delete operations and data-directory path rules from Tasks 5–7.
- Produces: `RetentionPolicy`, `CleanupCandidate`, `CleanupReport`, `Orchestrator.preview_cleanup()`, `Orchestrator.apply_cleanup()`, `POST /admin/cleanup`, `AcheronClient.cleanup()`, `acheron job archive`, and `acheron cleanup`.

- [ ] **Step 1: Write failing retention tests.**

Use a temporary data directory containing job cache, plan, output, and input files. Assert preview is non-mutating:

```python
preview = await retention.preview(
    RetentionPolicy(keep_successful=timedelta(days=7), keep_failed=timedelta(days=30)),
    now=now,
)

assert preview.deleted_count == 0
assert preview.reclaimable_bytes == expected_bytes
assert (data_dir / "jobs" / "job-old").exists()
```

Add apply tests for active-job refusal, symlink escape refusal, orphan-input deletion, retained-input preservation, and re-evaluation when a candidate becomes active between preview and apply.

- [ ] **Step 2: Run focused tests and verify failure.**

Run:

```bash
uv run pytest --no-cov tests/shell/test_retention.py tests/shell/test_cache.py tests/shell/test_input_store.py tests/shell/test_orchestrator.py tests/shell/api/test_jobs.py tests/shell/api/test_admin.py tests/test_api_client.py tests/shell/test_cli.py -q
```

Expected: failures because retention, input-reference, and deletion APIs do not exist.

- [ ] **Step 3: Add safe cache and plan deletion primitives.**

Add explicit methods to `StepCache`, `InMemoryStepCache`, and `PlanCache`:

```python
def job_size(self, job_id: str) -> int: ...
def delete_job(self, job_id: str) -> int: ...
def delete_plan(self, plan_id: str) -> int: ...
```

Each method resolves paths below the configured data directory, rejects symlink escapes, counts bytes before deletion, and removes only the named job/plan scope. Do not expose absolute paths in non-admin error responses.

- [ ] **Step 4: Implement the retention service.**

Create:

```python
@dataclass(frozen=True)
class RetentionPolicy:
    keep_successful: timedelta
    keep_failed: timedelta

@dataclass(frozen=True)
class CleanupCandidate:
    job_id: str
    status: PlanStatus
    relative_paths: tuple[str, ...]
    reclaimable_bytes: int

@dataclass(frozen=True)
class CleanupFailure:
    job_id: str
    relative_paths: tuple[str, ...]
    message: str


@dataclass(frozen=True)
class CleanupReport:
    apply: bool
    candidates: tuple[CleanupCandidate, ...]
    deleted_job_ids: tuple[str, ...]
    failures: tuple[CleanupFailure, ...]
    deleted_count: int
    deleted_bytes: int
    reclaimable_bytes: int
```

Select `COMPLETED` jobs older than `keep_successful` and `FAILED`/`PARTIAL` jobs older than `keep_failed`; archived terminal jobs remain eligible under the same status window and are shown as archived in the preview. Exclude `PENDING` and `RUNNING` unconditionally. Include the job record, plan, and job-scoped cache/artifact paths. Include an uploaded input only when no retained job references its normalized source path. Define one canonical data-directory-relative input identity and use it when upload metadata, `EpubRequest`/`AudioRequest`, retained-job references, and cleanup candidates are written or compared. Protect that identity with an input-level reference lock/refcount acquired by both submission/promotion and cleanup; cleanup must re-read references while holding the input lock before deleting a shared input.

Preview returns relative paths and sizes. Create one atomic per-job lifecycle-lock registry used by active execution, stale reaping, and cleanup; lock creation and acquisition occur under the registry mutex so two paths cannot create separate locks for the same job. Apply acquires that shared lock and the normalized-input reference lock, then rechecks status, active-task membership, archive eligibility, and path containment immediately before deletion. It deletes filesystem data before removing the job record. Use descriptor-relative, no-follow deletion (or an equivalent atomic containment primitive) so a symlink swap between eligibility and deletion cannot escape the data root. A failed filesystem deletion leaves the job record intact, appends `CleanupFailure` with relative paths, increments neither `deleted_count` nor `deleted_bytes`, and returns a retryable partial result rather than claiming success; already-absent paths are idempotent. Add tests for the lock race, per-job failure response, retry, normalized upload->submit->cleanup identity, concurrent shared-input submission, symlink-swap race, and unchanged preview.

- [ ] **Step 5: Add disk usage warnings.**

Extend `_verify_data_dir_writable()` to call `shutil.disk_usage(data_dir)` after the write probe. Log `WARNING` when free space is below 10% and `ERROR` when below 5%; do not turn a low-space warning into an unrelated write-probe exception. Add injectable disk-usage tests using `monkeypatch`.

- [ ] **Step 6: Add routes, client, and CLI.**

Define strict JSON-body cleanup request/response models with `apply: bool = False`, retention durations, candidate paths, `CleanupFailure.relative_paths`, per-job failures, `deleted_count`, `deleted_bytes`, and counts. Add `POST /admin/cleanup` behind `AdminTokenDep`, using `AdminErrorResponse` for request/auth/conflict failures and `AdminActionAudit` for success, failure, and partial results.

Add:

```python
@job.command("archive")
@click.argument("job_ids", nargs=-1, required=True)
def archive(job_ids: tuple[str, ...]) -> None: ...

@main.command()
@click.option("--keep-successful", required=True)
@click.option("--keep-failed", required=True)
@click.option("--apply", is_flag=True)
def cleanup(keep_successful: str, keep_failed: str, apply: bool) -> None: ...
```

Default CLI output is a preview. `--apply` is the only path that sends `apply=true`.

- [ ] **Step 7: Run focused tests and commit.**

Run:

```bash
uv run pytest --no-cov tests/shell/test_retention.py tests/shell/test_cache.py tests/shell/test_input_store.py tests/shell/test_orchestrator.py tests/shell/api/test_jobs.py tests/shell/api/test_admin.py tests/test_api_client.py tests/shell/test_cli.py -q
```

Expected: PASS. Commit:

```bash
git add src/acheron/shell/retention.py src/acheron/shell/cache.py src/acheron/shell/input_store.py src/acheron/shell/orchestrator.py src/acheron/shell/api/routes/admin.py src/acheron/api_client.py src/acheron/cli.py src/acheron/shell/api/schemas.py tests/shell/test_retention.py tests/shell/test_cache.py tests/shell/test_orchestrator.py tests/shell/api/test_admin.py tests/test_api_client.py tests/shell/test_cli.py
git commit -m "feat(MAINT-012,OPS-012): add safe retention cleanup"
```

### Task 9: Preserve worker recovery history and reset re-registration state (`MAINT-010`, `MAINT-011`)

**Files:**
- Modify: `src/acheron/core/models.py`
- Modify: `src/acheron/core/schemas.py`
- Modify: `src/acheron/shell/registry.py`
- Modify: `src/acheron/shell/stores/base.py`
- Modify: `src/acheron/shell/stores/memory.py`
- Modify: `src/acheron/shell/stores/redis.py`
- Modify: `src/acheron/shell/health.py`
- Modify: `src/acheron/shell/api/routes/workers.py`
- Modify: `src/acheron/shell/api/routes/capabilities.py`
- Modify: `tests/shell/stores/test_memory_worker_store.py`
- Modify: `tests/shell/stores/test_redis_worker_store.py`
- Modify: `tests/shell/test_health_monitor.py`
- Modify: `tests/shell/api/test_workers.py`
- Modify: `tests/shell/api/test_capabilities.py`
- Modify: `tests/integration/test_worker_registration.py`

**Interfaces:**
- Consumes: `RegisteredWorker`, existing health-store methods, and worker response mapping.
- Produces: `WorkerErrorEvent`, `RegisteredWorker.error_history`, worker-store history mutation signatures, and public bounded error history.

- [ ] **Step 1: Write failing worker lifecycle tests.**

Add a parameterized memory/Redis behavior test:

```python
await store.record_health_failure("tts-1", generation=1, error="connection refused")
await store.record_health_success("tts-1", generation=1)
worker = await store.get("tts-1")

assert worker.last_error is None
assert worker.consecutive_failures == 0
assert worker.error_history[-1].message == "connection refused"
```

Add a re-registration test that seeds stale status/failure fields, registers the same ID, and asserts current state resets while history remains capped and present.

- [ ] **Step 2: Run focused tests and verify failure.**

Run:

```bash
uv run pytest --no-cov tests/shell/stores/test_memory_worker_store.py tests/shell/stores/test_redis_worker_store.py tests/shell/test_health_monitor.py tests/shell/api/test_workers.py tests/shell/api/test_capabilities.py tests/integration/test_worker_registration.py -q
```

Expected: failures because worker records have no history, generation checks, or public redaction and `record_health_failure` has no error/generation arguments.

- [ ] **Step 3: Add the history value object and public schema.**

Define:

```python
@dataclass(frozen=True)
class WorkerErrorEvent:
    timestamp: datetime
    message: str
    consecutive_failures: int


class WorkerErrorEventResponse(BaseModel):
    timestamp: datetime
    message: str
    consecutive_failures: int
```

Add `registration_generation: int` to `RegisteredWorker`, incremented atomically on each registration, plus `error_history: tuple[WorkerErrorEvent, ...] = ()` and `error_history: list[WorkerErrorEventResponse]` to the public schema. Make the public `WorkerResponse.endpoint` omitted or `None`. Validate UTC timestamps and a maximum of 10 entries.

- [ ] **Step 4: Update health-store mutation signatures.**

Change the shared protocol to carry the registration generation captured by the health probe:

```python
async def record_health_failure(
    self,
    worker_id: str,
    *,
    generation: int,
    error: str,
) -> bool: ...
async def record_health_success(self, worker_id: str, *, generation: int) -> None: ...
```
Reject stale-generation updates when a worker re-registers with the same ID before an older probe result returns; the stale result must not mutate the new lifecycle or history.

Memory appends a sanitized event after incrementing the failure counter. Redis updates the hash and bounded JSON history in one transaction/Lua operation. `record_health_success` clears only current `last_error` and counters. Sanitization trims/bounds messages and removes credentials, traceback lines, URLs/host:port endpoint text, internal endpoints, bearer/token-like values, and provider request details before persistence. Add adversarial tests for each pattern and for both unauthenticated and registration-token callers.

Because the current stores remove a worker after three failures, preserve history in a bounded tombstone before removal: memory keeps `_worker_history_tombstones[worker_id]`, and Redis writes a dedicated history key before deleting the live worker hash. Tombstones contain only the capped sanitized history and are consumed by the next registration of the same worker ID; they are not returned as live workers. Redis tombstones use a bounded TTL and the memory store purges expired tombstones during registration/health maintenance so removed workers cannot accumulate unbounded history keys.

- [ ] **Step 5: Reset state atomically during re-registration.**

Memory registration creates a fresh current lifecycle from the new endpoint/capabilities while carrying history from either the live record or its tombstone, then consumes the tombstone. Redis reads history from the live record or dedicated tombstone, deletes the old hash, writes a complete new hash with zero failures, healthy status, empty current error, empty boot timestamp, and retained history, then deletes the consumed tombstone. Add an integration test for three health failures causing removal followed by re-registration: current lifecycle state resets while all bounded history remains.

Update `HealthMonitor` to pass the captured registration generation and sanitized provider/transport failure message into `record_health_failure`. Add a race test where a delayed probe result for generation N arrives after generation N+1 registers; the stale result must be ignored and must not increment failures or append history to the new worker.

- [ ] **Step 6: Map dashboard/API history and commit.**

Expose a sanitized history projection through unauthenticated `GET /workers`; remove the registration-token dependency from this read-only route and omit/redact endpoint fields in the public response. Map only bounded `WorkerErrorEventResponse` entries and sanitized current errors, never traceback text, credentials, internal endpoints, URLs, or provider request details. Apply the same public projection rule to `POST /workers` responses and `GET /capabilities`: registration responses return only worker identity/status, and capability responses allowlist worker type, languages, formats, limits, and canonical TTS speaker names while dropping arbitrary metadata and endpoint/provider fields. Add dashboard/API tests proving anonymous and registration-token callers receive the same sanitized public projection and cannot recover raw endpoint/error/capability details.

Run:

```bash
uv run pytest --no-cov tests/shell/stores/test_memory_worker_store.py tests/shell/stores/test_redis_worker_store.py tests/shell/test_health_monitor.py tests/shell/api/test_workers.py tests/shell/api/test_capabilities.py tests/integration/test_worker_registration.py -q
```

Expected: PASS. Commit:

```bash
git add src/acheron/core/models.py src/acheron/core/schemas.py src/acheron/shell/registry.py src/acheron/shell/stores src/acheron/shell/health.py src/acheron/shell/api/routes/workers.py tests/shell/stores tests/shell/test_health_monitor.py tests/shell/api/test_workers.py tests/integration/test_worker_registration.py
git commit -m "feat(MAINT-010,MAINT-011): preserve worker recovery history"
```

### Task 10: Complete recovery filters, dashboard operations, and UX evidence (`MAINT-001`, `MAINT-008`, `MAINT-010`, `MAINT-011`, `MAINT-012`, `OPS-012`)

**Files:**
- Modify: `src/acheron/api_client.py`
- Modify: `src/acheron/cli.py`
- Modify: `dashboard/app.py`
- Modify: `dashboard/templates/index.html`
- Modify: `dashboard/templates/partials/jobs.html`
- Modify: `dashboard/templates/partials/workers.html`
- Modify: `tests/test_api_client.py`
- Modify: `tests/shell/test_cli.py`
- Modify: `dashboard/tests/test_dashboard.py`
- Modify: `dashboard/tests/test_job_detail.py`
- Modify: `tests/integration/test_job_lifecycle.py`
- Modify: `docs/ux_review/ops.md`
- Modify: `docs/ux_review/maint.md`
- Modify: `docs/ux_review/summary.md`

**Interfaces:**
- Consumes: recovery APIs and store behavior from Tasks 5–9.
- Produces: complete operator journeys for stuck-job discovery, reaping, archive, cleanup, and worker recovery history.

- [ ] **Step 1: Write failing list/filter and dashboard tests.**

Add client/CLI tests for:

```text
acheron jobs --since 24h --status running --older-than 30m
acheron jobs --include-archived
acheron job archive job-old
```

Add dashboard tests that set the stuck toggle and older-than value, assert those query parameters reach the orchestrator, and render a worker’s last three history entries.

- [ ] **Step 2: Run focused tests and verify failure.**

Run:

```bash
uv run pytest --no-cov tests/test_api_client.py tests/shell/test_cli.py dashboard/tests/test_dashboard.py dashboard/tests/test_job_detail.py tests/integration/test_job_lifecycle.py -q
```

Expected: failures for missing client parameters, CLI options, and dashboard controls.

- [ ] **Step 3: Add client and CLI job filters.**

Extend `AcheronClient.list_jobs()` with typed keyword arguments:

```python
async def list_jobs(
    self,
    *,
    status: str | None = None,
    since: datetime | None = None,
    before: datetime | None = None,
    older_than_seconds: float | None = None,
    include_archived: bool = False,
) -> list[JobResponse]: ...
```

Add Click options that parse duration and ISO-8601 values before creating query parameters. Render archived status and stale age without changing the default exclusion of archived jobs. Render the API-provided `archived_at` value and assert archive preservation of the record, plan, outputs, inputs, and cost data.

- [ ] **Step 4: Add dashboard controls and worker history rendering.**

Use HTMX query parameters for job status/older-than/archive inclusion. Keep the default request as `/jobs` so existing polling behavior remains unchanged. Render the latest sanitized error and bounded history in the worker partial with sanitized message text, never endpoint or raw provider details. Cost rows and detail partials must label values as execution-time estimates and render unknown costs/counts explicitly.

- [ ] **Step 5: Run recovery journeys and story verification.**

Run:

```bash
uv run pytest --no-cov tests/test_api_client.py tests/shell/test_cli.py dashboard/tests/test_dashboard.py dashboard/tests/test_job_detail.py tests/integration/test_job_lifecycle.py -q
just ux-verify MAINT-001
just ux-verify MAINT-008
just ux-verify MAINT-010
just ux-verify MAINT-011
just ux-verify MAINT-012
just ux-verify OPS-012
```

Expected: no command returns `FAIL`; stories without harness artifacts may return `PARTIAL` before metadata exists. After the recovery journeys pass, update UX metadata, rerun all six commands, and require `PASS`. Commit:

```bash
git add src/acheron/api_client.py src/acheron/cli.py dashboard/app.py dashboard/templates tests docs/ux_review/ops.md docs/ux_review/maint.md docs/ux_review/summary.md
git commit -m "test(phase-4d): verify recovery administration journeys"
```

### Stage 2 checkpoint

- [ ] Admin mutations require the separate admin token in every auth mode.
- [ ] Stale jobs can be found and reaped without touching active tasks.
- [ ] Archive preserves records; cleanup previews and applies safe deletion.
- [ ] Disk pressure emits the 10% warning and 5% error thresholds.
- [ ] Memory and Redis stores agree on query, archive, delete, re-registration, and history behavior.
- [ ] All six Stage 2 `just ux-verify` commands pass.

---

## Stage 3 — Traceability and Deployed-Version Visibility

### Task 11: Add version identity contract and build metadata (`MAINT-016`)

**Files:**
- Create: `src/acheron/version.py`
- Modify: `src/acheron/core/schemas.py`
- Create: `src/acheron/shell/api/routes/version.py`
- Modify: `src/acheron/shell/api/app.py`
- Modify: `Dockerfile`
- Modify: `Dockerfile.edge`
- Modify: `docker-compose.yml`
- Modify: `README.md`
- Modify: `.env.example`
- Create: `tests/shell/api/test_version.py`
- Modify: `tests/integration/test_tls.py`

**Interfaces:**
- Consumes: package metadata and explicit build arguments.
- Produces: `VersionResponse`, `build_version()`, `GET /version`, and runtime build identity variables.

- [ ] **Step 1: Write failing version tests.**

Add tests using an environment fixture:

```python
def test_version_response_uses_explicit_build_identity(monkeypatch) -> None:
    monkeypatch.setenv("ACHERON_BUILD_SHA", "abc1234")
    monkeypatch.setenv("ACHERON_BUILD_TIME", "2026-07-30T12:00:00Z")
    monkeypatch.setenv("ACHERON_BUILD_BRANCH", "master")
    monkeypatch.setenv("ACHERON_BUILD_DIRTY", "false")

    version = build_version()

    assert version.sha == "abc1234"
    assert version.build_time == datetime(2026, 7, 30, 12, tzinfo=UTC)
    assert version.dirty is False
```

Add an endpoint test proving unset optional build values are `None` and no environment dump is returned.

- [ ] **Step 2: Run focused tests and verify failure.**

Run:

```bash
uv run pytest --no-cov tests/shell/api/test_version.py -q
```

Expected: failures because the module, response model, and route do not exist.

- [ ] **Step 3: Implement explicit build metadata resolution.**

Create `src/acheron/version.py` with a typed `VersionInfo` value object and `build_version()` that obtains the package version using `importlib.metadata.version("acheron")` and reads only the named build variables:

```text
ACHERON_BUILD_SHA
ACHERON_BUILD_TIME
ACHERON_BUILD_BRANCH
ACHERON_BUILD_DIRTY
ACHERON_BUILD_IMAGE
ACHERON_BUILD_REGISTRY
```

Return `None` for unset optional values. Parse `ACHERON_BUILD_DIRTY` strictly from `true`/`false`; reject malformed values during app construction with a chained `ValueError`.

- [ ] **Step 4: Add the route and image build arguments.**

Add `VersionResponse` and register `GET /version` as a read-only route. Add Docker `ARG`/`ENV` pairs to both orchestrator and edge images. Pass optional build args through Compose without making them required deployment secrets. Document the fields and the fact that they are identity metadata, not runtime configuration.

- [ ] **Step 5: Run focused tests and commit.**

Run:

```bash
uv run pytest --no-cov tests/shell/api/test_version.py tests/integration/test_tls.py -q
```

Expected: PASS. Commit:

```bash
git add src/acheron/version.py src/acheron/core/schemas.py src/acheron/shell/api/routes/version.py src/acheron/shell/api/app.py Dockerfile Dockerfile.edge docker-compose.yml README.md .env.example tests/shell/api/test_version.py tests/integration/test_tls.py
git commit -m "feat(MAINT-016): expose deployed version identity"
```

### Task 12: Propagate response request IDs and improve URL diagnostics (`MAINT-013`, `OPS-022`)

**Files:**
- Modify: `src/acheron/shell/api/app.py`
- Modify: `src/acheron/api_client.py`
- Modify: `src/acheron/cli.py`
- Modify: `tests/test_api_client.py`
- Modify: `tests/shell/api/test_app.py`
- Modify: `tests/integration/test_cli_errors.py`
- Modify: `tests/shell/test_cli.py`

**Interfaces:**
- Consumes: existing request-ID middleware and `AcheronClient` response handling.
- Produces: response `x-request-id`, `AcheronClient.last_request_id`, CLI stderr correlation output, and attempted-URL HTTP errors.

- [ ] **Step 1: Write failing request-ID and URL tests.**

Add a middleware test:

```python
response = await client.get("/health", headers={"x-request-id": "req-test"})

assert response.headers["x-request-id"] == "req-test"
```

Add a generated-ID test, client capture test, and CLI URL-redaction tests for connection, timeout, HTTP-status, follow-up, and streaming/watch error paths, each asserting an attempted URL with credentials, query, and fragment removed:

```text
Error 404: Not Found (from https://wrong.host/jobs/job-abc) — verify ACHERON_URL
request_id=req-test
```

- [ ] **Step 2: Run focused tests and verify failure.**

Run:

```bash
uv run pytest --no-cov tests/test_api_client.py tests/shell/api/test_app.py tests/integration/test_cli_errors.py tests/shell/test_cli.py -q
```

Expected: failures because response headers are not set, the client discards headers, and status/streaming errors omit sanitized URLs.

- [ ] **Step 3: Set the response header in middleware.**

After `call_next(request)`, set the response header using the same ID bound to logging:

```python
response = await call_next(request)
response.headers["x-request-id"] = request_id
return response
```

Do not create a second ID after the handler completes.

- [ ] **Step 4: Capture IDs in the client and print them from the CLI.**

Add `last_request_id: str | None` to `AcheronClient`. Every non-streaming response and the initial streaming response calls `_remember_response(response)` before status handling. Define a command-scoped client lifetime: each Click command constructs one client and passes it through all operations, including submit/preview/follow and status/watch, so `last_request_id` is read from the same instance. For streaming, capture the initial response ID and print it once; subsequent events do not overwrite the command correlation.

Add `_sanitize_attempted_url()` as the single URL-display boundary for `_print_http_error()`, connection errors, timeout errors, specialized renderers, follow-up/watch errors, `_run_sync_generator()`/tail protocol failures, non-JSON `str(exc)` fallbacks, and dashboard-facing diagnostics: preserve scheme/host/path, remove userinfo, query, and fragment, and never print raw `exc.request.url` or raw `ACHERON_URL`. Append the `ACHERON_URL` remediation for status errors. Keep one command-scoped client through streaming/watch and error handlers. Add tests proving credentials, query secrets, and raw URL text are absent on every path, including generator and non-JSON failures.

- [ ] **Step 5: Run focused tests and commit.**

Run:

```bash
uv run pytest --no-cov tests/test_api_client.py tests/shell/api/test_app.py tests/integration/test_cli_errors.py tests/shell/test_cli.py -q
```

Expected: PASS. Commit:

```bash
git add src/acheron/shell/api/app.py src/acheron/api_client.py src/acheron/cli.py tests/test_api_client.py tests/integration/test_cli_errors.py tests/shell/test_cli.py
git commit -m "feat(MAINT-013,OPS-022): correlate requests and URLs"
```

### Task 13: Render version and traceability in the dashboard and verify stories (`MAINT-016`, `MAINT-013`, `OPS-022`)

**Files:**
- Modify: `dashboard/app.py`
- Modify: `dashboard/templates/index.html`
- Create or modify: `dashboard/templates/partials/version.html`
- Modify: `dashboard/tests/test_dashboard.py`
- Modify: `docs/ux_review/ops.md`
- Modify: `docs/ux_review/maint.md`
- Modify: `docs/ux_review/summary.md`

**Interfaces:**
- Consumes: `/version` and request-correlated client behavior from Tasks 11–12.
- Produces: dashboard deployed-version header and story-scoped traceability evidence.

- [ ] **Step 1: Write failing dashboard tests.**

Mock `/version` with:

```json
{
  "version": "0.1.0",
  "sha": "abc1234",
  "build_time": "2026-07-30T12:00:00Z",
  "branch": "master",
  "dirty": false,
  "image": "acheron:dev",
  "registry": null
}
```

Assert the rendered header contains `v0.1.0 (sha-abc1234)` and no secret/config fields. Assert a failed version fetch renders a neutral unknown identity rather than failing the dashboard page.

- [ ] **Step 2: Run focused tests and verify failure.**

Run:

```bash
uv run pytest --no-cov dashboard/tests/test_dashboard.py -q
```

Expected: failure because the dashboard does not fetch or render version identity.

- [ ] **Step 3: Implement the dashboard version partial.**

Add a safe fetch path with the existing disconnected behavior. Render only version and short SHA in the header; retain full identity in the partial if useful for operator inspection. Do not proxy admin credentials from the dashboard.

- [ ] **Step 4: Run story-scoped verification and commit.**

Run:

```bash
uv run pytest --no-cov dashboard/tests/test_dashboard.py -q
just ux-verify OPS-022
just ux-verify MAINT-013
just ux-verify MAINT-016
```

Expected: no command returns `FAIL`; stories without harness artifacts may return `PARTIAL` before metadata exists. After the traceability/version journeys pass, update their UX metadata, rerun all three commands, and require `PASS`. Commit:

```bash
git add dashboard/app.py dashboard/templates dashboard/tests/test_dashboard.py docs/ux_review/ops.md docs/ux_review/maint.md docs/ux_review/summary.md
git commit -m "test(phase-4d): verify traceability surfaces"
```

### Stage 3 checkpoint

- [ ] `/version` exposes only explicit build identity.
- [ ] Every API response carries the request ID used for logging.
- [ ] The client captures the ID and CLI prints it to stderr.
- [ ] HTTP status errors include the attempted URL and URL remediation.
- [ ] Dashboard version identity and disconnected behavior pass tests.
- [ ] All three Stage 3 `just ux-verify` commands pass.

---

## Stage 4 — Job-Level Voice Selection

### Task 14: Define voice value objects and strict submission contracts (`OPS-028`)

**Files:**
- Modify: `src/acheron/core/models.py`
- Modify: `src/acheron/core/schemas.py`
- Modify: `src/acheron/shell/api/schemas.py`
- Modify: `src/acheron/shell/api/routes/inputs.py`
- Modify: `src/acheron/api_client.py`
- Modify: `src/acheron/cli.py`
- Modify: `src/acheron/shell/stores/redis.py`
- Modify: `src/acheron/shell/stores/memory.py`
- Modify: `tests/core/test_models.py`
- Modify: `tests/core/test_schemas.py`
- Modify: `tests/shell/api/test_schemas.py`
- Modify: `tests/shell/api/test_inputs.py`
- Modify: `tests/test_api_client.py`
- Modify: `tests/shell/test_cli.py`
- Modify: `tests/shell/stores/test_memory_job_store.py`

**Interfaces:**
- Consumes: existing `EpubRequest`, `AudioRequest`, strict request base models, and CLI submission flow.
- Produces: `VoiceRange`, `VoiceSelection`, API `voice`/`voice_map` fields, client parameters, and CLI parser output.

- [ ] **Step 1: Write failing voice parsing tests.**

Add core tests:

```python
def test_voice_range_rejects_overlap() -> None:
    with pytest.raises(ValueError, match="overlap"):
        VoiceSelection.from_ranges(
            default_voice=None,
            ranges=(
                VoiceRange(1, 3, "Vivian"),
                VoiceRange(3, 5, "Ryan"),
            ),
            chapter_count=5,
        )
```

Add API tests proving unknown fields are rejected, audio rejects `voice_map`, and EPUB accepts structured ranges. Add CLI tests for `--voice Vivian` and repeated `--voice-map 1-3:Vivian --voice-map 4-100:Ryan`.

- [ ] **Step 2: Run focused tests and verify failure.**

Run:

```bash
uv run pytest --no-cov tests/core/test_models.py tests/core/test_schemas.py tests/shell/api/test_schemas.py tests/shell/api/test_inputs.py tests/test_api_client.py tests/shell/test_cli.py tests/shell/stores/test_memory_job_store.py -q
```

Expected: failures because requests, temporary-input lifecycle, persistence, and parsing have no voice fields.

- [ ] **Step 3: Add typed voice values and validation.**

Define:

```python
@dataclass(frozen=True)
class VoiceRange:
    start_chapter: int
    end_chapter: int
    voice: str

@dataclass(frozen=True)
class VoiceSelection:
    default_voice: str | None
    ranges: tuple[VoiceRange, ...]
```

In `src/acheron/shell/api/schemas.py`, define the strict wire model:

```python
class VoiceRangeRequest(BaseModel):
    start_chapter: int
    end_chapter: int
    voice: str
```

Add `VoiceSelection.from_ranges(default_voice, ranges, chapter_count)` to reject non-positive chapters, reversed ranges, overlap, ranges beyond the discovered chapter count, and uncovered chapters when no default is provided. Keep canonical voice spelling separate from user-entered casing.

Add `voice: str | None` to both domain request dataclasses; add `voice_map: tuple[VoiceRange, ...] = ()` only to `EpubRequest`. Keep `VoiceRangeRequest` as the strict wire model, and explicitly convert API/CLI ranges to canonical `VoiceRange` values before constructing the domain request. The route accepts the wire field only when `source_type="epub"`, rejects it for audio before plan compilation, and persists the domain request fields through memory/Redis retry/resume serialization.

- [ ] **Step 4: Add client and CLI request plumbing.**

Extend `AcheronClient.submit_job()` and `preview_job()` with `voice` and `voice_map` parameters. Serialize structured API ranges as objects:

```json
{
  "start_chapter": 1,
  "end_chapter": 3,
  "voice": "Vivian"
}
```

Add Click options:

```python
@click.option("--voice", default=None)
@click.option("--voice-map", multiple=True, help="Inclusive chapter range, e.g. 1-3:Vivian")
```

Parse and validate `--voice`/`--voice-map` with a strict `START-END:VOICE` grammar before calling `upload_input()` or any preview/submit request. Grammar validation is only the local preflight: the API then uses the existing non-job `POST /jobs:preview` flow to discover EPUB chapter count and jointly capable worker records before creating/persisting the job or plan. Extend `InputResponse` with a temporary-input identity and add an authenticated idempotent `DELETE /inputs/{input_id}`; preview inputs are never attached to a job, successful submission atomically promotes the input to the job, and failure/timeout/cancellation deletes the temporary input. If promotion fails, the server deletes the temporary input and creates no job. Report the offending value as a Click usage error and assert in CLI tests that no upload or submission request occurs on invalid grammar; add API/client tests for temporary cleanup, promotion, timeout, and no leaked input. Convert valid values to canonical domain fields before the server preflight and final submit.

- [ ] **Step 5: Persist request voice fields.**

Update Redis and memory job serialization so retry/resume records retain the original default voice and normalized map. Add the same parameterized round-trip test for Redis and memory stores for EPUB and audio requests; do not consider voice persistence complete until both backends pass.

- [ ] **Step 6: Run focused tests and commit.**

Run:

```bash
uv run pytest --no-cov tests/core/test_models.py tests/core/test_schemas.py tests/shell/api/test_schemas.py tests/shell/api/test_inputs.py tests/test_api_client.py tests/shell/test_cli.py tests/shell/stores/test_memory_job_store.py -q
```

Expected: PASS. Commit:

```bash
git add src/acheron/core/models.py src/acheron/core/schemas.py src/acheron/shell/api/schemas.py src/acheron/api_client.py src/acheron/cli.py src/acheron/shell/stores/redis.py tests/core tests/shell/api/test_schemas.py tests/test_api_client.py tests/shell/test_cli.py
git commit -m "feat(OPS-028): define job voice selection requests"
```

### Task 15: Validate voices during planning and select one jointly capable worker (`OPS-028`)

**Files:**
- Modify: `src/acheron/core/planner.py`
- Modify: `src/acheron/shell/step_handler.py`
- Modify: `src/acheron/shell/orchestrator.py`
- Modify: `src/acheron/core/errors.py`
- Modify: `tests/core/test_planner.py`
- Modify: `tests/shell/test_orchestrator.py`
- Modify: `tests/shell/test_step_handler.py`

**Interfaces:**
- Consumes: `VoiceSelection`, strict request fields, and `WorkerCapabilities.metadata["speakers"]` from Task 14.
- Produces: canonical voice selection in `PlanStep.payload`, `VoiceSelectionError`, and voice-aware worker selection.

- [ ] **Step 1: Write failing planner and dispatcher tests.**

Create TTS capability fixtures:

```python
vivian_ryan = capabilities(
    worker_type=WorkerType.TTS,
    metadata={"speakers": ["Vivian", "Ryan"]},
)
serena = capabilities(
    worker_type=WorkerType.TTS,
    metadata={"speakers": ["Serena"]},
)
```

Assert a `Vivian`/`Ryan` EPUB map selects the jointly capable worker, an unsupported voice fails before plan persistence, and two workers that separately advertise the requested voices do not satisfy the single-worker contract.

- [ ] **Step 2: Run focused tests and verify failure.**

Run:

```bash
uv run pytest --no-cov tests/core/test_planner.py tests/shell/test_orchestrator.py tests/shell/test_step_handler.py -q
```

Expected: failures because planner and dispatcher ignore voice metadata.

- [ ] **Step 3: Add canonical capability matching.**

Implement focused helpers in `planner.py` over worker-ID/capability records:

```python
type WorkerCapabilityRecord = tuple[str, WorkerCapabilities]

def advertised_voices(capabilities: WorkerCapabilities) -> frozenset[str]: ...
def canonicalize_voice(name: str, capabilities: WorkerCapabilities) -> str: ...
def select_voice_worker_id(
    selection: VoiceSelection,
    workers: tuple[WorkerCapabilityRecord, ...],
) -> str: ...
```

Update the planner/orchestrator seam so the pre-persistence compile/preflight call receives worker IDs with capabilities and performs voice selection before `create_job()` or plan persistence. Unsupported voices or the absence of one jointly capable worker must raise `VoiceSelectionError` before either record exists. The dispatcher receives the selected worker ID and may only enforce that already-selected capability, never perform the first voice validation after persistence. Never infer IDs from capability objects. Match case-insensitively but return the registered canonical spelling. Raise `VoiceSelectionError` with a sanitized message naming the requested voice and available voices. Filter to TTS records that jointly contain the full selected voice set, choose one deterministically, return its worker ID, and fail if no record is jointly capable or if dispatch cannot honor the selected ID.

- [ ] **Step 4: Encode the selection into the TTS step.**

When compiling an EPUB plan, add only canonical voice data to the `synthesize` step payload:

```python
{
    "target_language": request.target_language,
    "voice": selection.default_voice,
    "voice_map": [
        {
            "start_chapter": item.start_chapter,
            "end_chapter": item.end_chapter,
            "voice": item.voice,
        }
        for item in selection.ranges
    ],
}
```

For audio, add only the canonical default voice. Store the planner-selected worker ID in an internal `PlanStep.selected_worker_id`/dispatch context that is not serialized into public `PlanResponse`; the dispatcher must use that ID rather than selecting the first matching worker. Do not expose payload details in the public `PlanResponse`.

- [ ] **Step 5: Make the step handler voice-aware.**

In `CachingStepHandler.__call__`, require the internal planner-selected worker ID for TTS steps and verify that worker still advertises every required voice before dispatch; do not fall back to the first matching worker. Keep existing language matching unchanged for non-TTS workers. Preserve the one-worker selection rule and worker ID attribution.

- [ ] **Step 6: Run focused tests and commit.**

Run:

```bash
uv run pytest --no-cov tests/core/test_planner.py tests/shell/test_orchestrator.py tests/shell/test_step_handler.py -q
```

Expected: PASS. Commit:

```bash
git add src/acheron/core/planner.py src/acheron/shell/step_handler.py src/acheron/shell/orchestrator.py src/acheron/core/errors.py tests/core/test_planner.py tests/shell/test_orchestrator.py tests/shell/test_step_handler.py
git commit -m "feat(OPS-028): route plans to voice-capable workers"
```

### Task 16: Apply voice maps per chunk in the TTS worker (`OPS-028`)

**Files:**
- Modify: `workers/qwen3tts/handler.py`
- Modify: `workers/_shared_utils.py` only if a shared range resolver is needed by tests and worker code
- Modify: `workers/qwen3tts/tests/test_handler.py`
- Modify: `tests/worker_sdk/test_schemas.py`

**Interfaces:**
- Consumes: canonical `voice` and `voice_map` step payloads from Task 15 and parsed chunk chapter IDs.
- Produces: one speaker value per input chunk passed to `generate_custom_voice()`.

- [ ] **Step 1: Write failing worker tests.**

Use a fake Qwen model and chunks from chapters 1 and 4:

```python
artifacts = await handler.handle(
    job_with_payload(
        target_language="en",
        voice="Ryan",
        voice_map=[
            {"start_chapter": 1, "end_chapter": 3, "voice": "Vivian"},
            {"start_chapter": 4, "end_chapter": 100, "voice": "Ryan"},
        ],
    ),
    chunks_input,
)

assert fake_model.speakers == ["Vivian", "Ryan"]
```

Add tests for default-only voice, uncovered chapter with default, invalid chapter ID, and worker-side rejection of a voice absent from its advertised set.

- [ ] **Step 2: Run focused tests and verify failure.**

Run:

```bash
uv run pytest --no-cov workers/qwen3tts/tests/test_handler.py tests/worker_sdk/test_schemas.py -q
```

Expected: failures because `_resolve_speaker()` currently returns one speaker for the entire batch.

- [ ] **Step 3: Implement per-chunk voice resolution.**

Replace the single speaker calculation with a resolver that reads canonical plan keys `voice` and `voice_map`, maps each parsed chunk’s `chapter_id` to an inclusive numeric chapter, applies the first matching normalized range, and falls back to the default voice. At this worker boundary, map the resolved canonical `voice` value to the Qwen model’s `speaker` argument; do not rename the canonical plan payload keys. Reject a malformed chapter ID or missing voice deterministically. Validate each resolved voice against `_ALL_SPEAKERS` before inference begins.

Build:

```python
speakers = [self._resolve_speaker_for_chunk(chunk, job, target_lang) for chunk in chunks]
```

Pass the resulting list to `generate_custom_voice()` while preserving chunk order and artifact metadata.

- [ ] **Step 4: Run focused worker tests and commit.**

Run:

```bash
uv run pytest --no-cov workers/qwen3tts/tests/test_handler.py tests/worker_sdk/test_schemas.py -q
```

Expected: PASS. Commit:

```bash
git add workers/qwen3tts/handler.py workers/_shared_utils.py workers/qwen3tts/tests/test_handler.py tests/worker_sdk/test_schemas.py
git commit -m "feat(OPS-028): apply voice maps per chapter"
```

### Task 17: Complete voice integration journey and UX evidence (`OPS-028`)

**Files:**
- Modify: `src/acheron/shell/api/routes/jobs.py`
- Modify: `src/acheron/api_client.py`
- Modify: `src/acheron/cli.py`
- Modify: `tests/shell/api/test_jobs.py`
- Modify: `tests/integration/test_job_lifecycle.py`
- Modify: `tests/shell/test_cli.py`
- Modify: `docs/ux_review/ops.md`
- Modify: `docs/ux_review/summary.md`

**Interfaces:**
- Consumes: voice request, planner, dispatcher, and worker behavior from Tasks 14–16.
- Produces: an end-to-end valid/invalid voice-selection journey and verified `OPS-028` evidence.

- [ ] **Step 1: Write the integration journey.**

Create an EPUB fixture with at least four numbered chapters and register a TTS worker advertising `Vivian` and `Ryan`. Upload as a temporary input, run the server-side non-job `POST /jobs:preview` preflight first so chapter discovery and worker canonicalization happen before job/plan persistence, then promote the same input during submission. On every rejected, timed-out, or failed preflight path, call the idempotent temporary-input deletion operation and assert no leaked input or job remains. Submit:

```bash
acheron job submit book.epub \
  --src en --dest es \
  --voice-map 1-3:Vivian \
  --voice-map 4-4:Ryan
```

Assert the plan contains canonical `voice`/`voice_map` payload data, the selected worker is the jointly capable worker, and the worker boundary maps each canonical voice to the Qwen model `speaker` argument and receives the expected speaker sequence. Assert that a request using separate workers for `Vivian` and `Ryan` fails during preflight with no job record or persisted plan; if temporary upload was required, assert cleanup.

- [ ] **Step 2: Run focused and integration tests.**

Run:

```bash
uv run pytest --no-cov tests/shell/api/test_jobs.py tests/integration/test_job_lifecycle.py tests/shell/test_cli.py workers/qwen3tts/tests/test_handler.py -q
```

Expected: PASS.

- [ ] **Step 3: Run story verification and commit.**

Run:

```bash
just ux-verify OPS-028
```

Expected: `PARTIAL` is acceptable before metadata because this story has no harness artifact; `FAIL` is a blocker. After the voice journey passes, update `OPS-028` metadata, rerun `just ux-verify OPS-028`, and require `PASS`. Commit:

```bash
git add src/acheron/shell/api/routes/jobs.py src/acheron/api_client.py src/acheron/cli.py tests/shell/api/test_jobs.py tests/integration/test_job_lifecycle.py tests/shell/test_cli.py docs/ux_review/ops.md docs/ux_review/summary.md
git commit -m "test(OPS-028): verify job-level voice selection"
```

### Stage 4 checkpoint

- [ ] Default voices and EPUB range maps have strict request validation.
- [ ] Voice names canonicalize against worker metadata.
- [ ] One TTS worker must jointly advertise all selected voices.
- [ ] The linear TTS stage applies the correct voice per chunk.
- [ ] Invalid voice requests fail before plan persistence or job creation.
- [ ] `just ux-verify OPS-028` passes.

---

### Task 18: Final UX metadata, full repository gates, and independent review

**Files:**
- Modify: `src/acheron/ux_review/verify.py`
- Create: `tests/ux_review/test_verify.py`
- Modify: `docs/ux_review/ops.md`
- Modify: `docs/ux_review/maint.md`
- Modify: `docs/ux_review/summary.md`
- Modify: any source/test files required by verification findings only

**Interfaces:**
- Consumes: all completed Phase 4D stages and story-scoped evidence.
- Produces: final green validation, refreshed UX metadata, and a clean correctness/documentation review.

- [ ] **Step 1: Run the complete project gates.**

Run in order:

```bash
just lint-strict
just type-check
just test
just validate
just ux-validate
just first-run
```

If the implementation changes the exercised RunPod worker image or registration path, also run `just runpod-bootstrap` and the relevant simulator journey. Otherwise, retain the focused pricing simulations from Task 4 as the Phase 4D provider evidence.

- [ ] **Step 2: Run a pre-metadata UX verification pass.**

Run the 15 `just ux-verify` commands for all Phase 4D stories. `FAIL` is a blocker; `PARTIAL` is expected for stories discovered through feedback/code review before `verified_by` or `last_verified_at` metadata exists. Do not claim `PASS` or mark metadata from this preflight.

- [ ] **Step 3: Refresh UX metadata and aggregate counts after evidence.**

After the required story journeys, simulations, and focused tests pass, update `fixed_in`, `verified_in`, `last_verified_at`, and `verified_by` with the actual implementation/evidence commit used by the current diff. Update `docs/ux_review/summary.md` counts and status lists. Do not mark a story verified from a focused unit test alone. Change `src/acheron/ux_review/verify.py` so `PASS` requires `last_verified_at["commit"] == head_sha` (and the evidence commit is present in `verified_in`); stale or unrelated metadata must return `PARTIAL`/`FAIL`, not `PASS`. Add unit tests for matching, stale, missing, and harness-artifact cases before the final verification loop.

- [ ] **Step 4: Run the final UX verification pass.**

Run the verifier metadata tests, then rerun all 15 `just ux-verify` commands and require `PASS` for each. Run `just ux-validate` again after metadata and summary changes; any stale metadata or non-matching evidence commit is a gate failure.

- [ ] **Step 5: Perform independent review.**

Review the complete diff for:

- registration/admin-token separation and open-registration behavior;
- symlink-safe cleanup and active-job exclusion;
- cost truthfulness and unknown-vs-stub distinctions;
- Redis/memory parity and restart persistence;
- sanitized public errors and request-ID consistency;
- voice range validation and single-worker enforcement;
- stale documentation or UX metadata claims.

Resolve verified findings with focused tests before finalizing. Re-run the affected gate after each fix.

- [ ] **Step 6: Final status check.**

Run:

```bash
git status --short
just validate
just ux-validate
```

Expected: implementation/test/docs changes are intentional, validation passes, and the UX rubric is valid at `HEAD`.

Do not create a merge commit or modify ignored developer-specific files as part of this plan.

---

## Completion Checklist

- [ ] All 15 Phase 4D stories have implementation coverage in this plan.
- [ ] Cost estimates preserve rate metadata, cache age, GPU identity, and basis truthfully.
- [ ] Stub/local zero pricing is distinct from static pricing and unknown pricing.
- [ ] Admin actions use a separate token and are auditable by request ID.
- [ ] Stale jobs can be found, reaped, archived, and cleaned safely.
- [ ] Memory and Redis stores persist equivalent job and worker records.
- [ ] Worker error history survives probe recovery and re-registration state reset.
- [ ] Disk pressure thresholds and retention preview/apply behavior are tested.
- [ ] Version identity and request correlation are visible in API, CLI, and dashboard surfaces.
- [ ] Voice maps are strict, canonicalized, jointly capability-validated, and applied per chunk.
- [ ] All focused tests, repository gates, first-run checks, and 15 UX verification commands pass.
- [ ] UX metadata and summary counts reference the current implementation/evidence commit and are refreshed only after behavior/journey evidence, followed by a final PASS for all 15 `ux-verify` commands.
- [ ] Independent correctness and documentation-staleness review is complete.
