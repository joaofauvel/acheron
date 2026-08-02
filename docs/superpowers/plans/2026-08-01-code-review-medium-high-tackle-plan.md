# Code Review Medium/High Tackle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` to implement this plan task-by-task. Each task is one topology bundle; use one writer for the active worktree and fresh read-only reviewers after every worker pass.

**Goal:** Resolve all remaining open high- and medium-severity review stories in nine topology-ordered bundles, with up to five fresh review rounds per bundle and an atomic implementation commit after verification.

**Status:** Complete. All 21 selected stories are verified; the final code gate and broad branch review passed.

**Architecture:** Preserve existing core/shell/worker boundaries while repairing shared orchestration state, event lifecycle ownership, public schema boundaries, operational persistence, dashboard URL separation, worker resource handling, validation coverage, and developer gates. Each bundle was implemented by one worker where practical, reviewed by fresh agents, corrected by one fix worker when needed, and committed atomically by the parent with metadata follow-ups.

**Tech Stack:** Python 3.14, FastAPI, Pydantic 2, asyncio, Redis/in-memory stores, httpx, Jinja2 dashboard, pytest, Ruff, mypy, basedpyright, import-linter, Just.

## Global Constraints

- Include only the 21 stories listed in the approved design; exclude stale, fixed, verified, and wontfix stories.
- Work only in `.worktrees/code-review-medium-high` on `fix/code-review-medium-high`; never write to `master`.
- One mutation-capable worker writes the active worktree at a time; reviewers are fresh-context and read-only.
- Behavior changes use test-first development: add a failing behavioral test, run it, implement the minimum fix, then refactor only while green.
- Follow the repository's greenfield rule: replace obsolete paths instead of adding compatibility shims or silent fallback behavior.
- Do not add `Any`, broad exception swallowing, string dispatch, misleading configuration knobs, or unexplained type/lint ignores.
- Run `just validate` after each bundle's accepted changes; run focused tests before the full gate.
- Apply formal correctness and doc-staleness passes before setting stories to `verified`.
- Commit each bundle's implementation changes atomically with the relevant story ID in scope; grouped stories sharing files were kept together, followed by parent-owned review-metadata commits.
- Review-loop cap is five rounds per bundle; stop early when no blocker or worthwhile fix remains and escalate unapproved architecture/product decisions.
- Do not run the generic Poetry/dbt gate: Poetry and dbt are unavailable, and this repository has no dbt project. `just validate` is authoritative.

---

### Task 1: Orchestration and cache boundaries (`ARCH-027`, `ARCH-028`, `PERF-014`)

**Files:**
- Modify: `src/acheron/shell/orchestrator.py`
- Modify: `src/acheron/shell/step_handler.py`
- Modify: `src/acheron/shell/transports/http.py`
- Modify: `src/acheron/shell/api/app.py`
- Modify: `src/acheron/shell/cache.py`
- Modify: `src/acheron/shell/api/routes/job_outputs.py`
- Test: `tests/shell/test_orchestrator.py`
- Test: `tests/shell/test_step_handler.py`
- Test: `tests/shell/test_cache.py`
- Test: `tests/shell/test_http_worker.py`
- Test: `tests/shell/transports/test_http_multipart.py`
- Test: `tests/shell/api/test_app.py`
- Test: `tests/integration/test_worker_integration.py`
- Review entries: `docs/code_review/architecture.md`, `docs/code_review/operations.md`

**Interfaces:**
- `Orchestrator`, `create_step_handler`, `default_worker_factory`, and `HttpWorker` consume one shared `StepCache` for upstream manifests.
- Application-owned `PlanCache`, `StepCache`, input storage, output serving, and orchestrator settings use one canonical resolved data directory.
- Worker transport instances remain cached across jobs until registry state changes.

- [x] **Step 1: Add failing cache-boundary tests.**
  - Construct an orchestrator with a disk `StepCache`, dispatch an upstream step, and assert an `HttpWorker` created by the default factory reads that manifest.
  - Construct `create_app()` with a caller-supplied `PlanCache` rooted differently from `settings.orchestrator.data_dir` and assert construction raises the repository's configuration error.
  - Submit two jobs without registry changes and assert the HTTP client/channel factory is called once; change registry generation and assert the old worker resources are retired and rebuilt.
- [x] **Step 2: Run the focused red tests.**
  - Run `uv run pytest tests/shell/test_orchestrator.py tests/shell/test_step_handler.py tests/shell/test_cache.py tests/shell/test_http_worker.py tests/shell/transports/test_http_multipart.py tests/shell/api/test_app.py tests/integration/test_worker_integration.py -q`.
  - Confirm failures identify missing shared-cache wiring, root mismatch handling, or over-eager invalidation.
- [x] **Step 3: Implement the shared cache and canonical-root contract.**
  - Pass the orchestrator-owned `StepCache` through `create_step_handler` and `default_worker_factory` into `HttpWorker`; remove the transport's independent cache construction.
  - Resolve the settings data directory once, require any supplied `PlanCache` to match it, and use that same root for plan, step, input, artifact, and output paths.
  - Replace per-submission pool clearing with registry-generation-aware invalidation while retaining existing close behavior for retired clients.
- [x] **Step 4: Run focused tests and inspect resource ownership.**
  - Re-run the command from Step 2 and verify both the remote upstream-manifest path and unchanged-registry resource reuse.
- [x] **Step 5: Run `just validate`.**
- [x] **Step 6: After review-loop approval, stage code/tests and the three story entries; commit separately:**
  - `fix(ARCH-027): share step cache with remote workers`
  - `fix(ARCH-028): enforce one canonical data root`
  - `fix(PERF-014): retain transport clients across jobs`

---

### Task 2: Job-event lifecycle (`MAINT-024`, `CORR-045`, `PERF-012`, `TEST-033`)

**Files:**
- Modify: `src/acheron/shell/job_events.py`
- Modify: `src/acheron/shell/api/routes/jobs.py`
- Test: `tests/shell/test_job_events.py`
- Test: `tests/shell/api/test_jobs.py`
- Review entries: `docs/code_review/code-quality.md`, `docs/code_review/correctness.md`, `docs/code_review/operations.md`, `docs/code_review/verification.md`

**Interfaces:**
- `JobEventBroker.subscribe()` returns a stream that terminates for jobs already finished and for jobs finishing after subscription.
- `publish()` and `finish()` retain bounded state only for active jobs and remove finished buffers/subscriber queues.
- The jobs log route uses the broker's atomic terminal-state operation instead of a status-check/subscribe race.

- [x] **Step 1: Add failing event-lifecycle tests.**
  - Finish a job before subscribing and assert buffered events followed by the terminal sentinel.
  - Schedule a barrier between the route status check and subscription, finish the job, and assert the streamed response terminates.
  - Publish many events, disconnect subscribers, finish the job, and assert completed buffers and subscriber registrations are removed and queue size remains bounded.
- [x] **Step 2: Run the focused red tests.**
  - Run `uv run pytest tests/shell/test_job_events.py tests/shell/api/test_jobs.py -q` and confirm the late subscriber hangs or retains state before the fix.
- [x] **Step 3: Implement atomic terminal handling and bounded cleanup.**
  - Track finished job state long enough for a late subscriber to receive a terminal sentinel, or perform subscription against a single synchronized broker state transition.
  - Bound subscriber queues, unregister them when the stream closes, and evict completed event buffers after terminal delivery.
  - Keep event ordering and the existing NDJSON response contract unchanged.
- [x] **Step 4: Run focused tests, including cancellation/disconnect paths.**
  - Re-run the command from Step 2 and verify no task or queue remains after the stream exits.
- [x] **Step 5: Run `just validate`.**
- [x] **Step 6: After review-loop approval, commit the four stories separately:**
  - `fix(MAINT-024): reclaim completed job event state`
  - `fix(CORR-045): terminate late job log subscribers`
  - `fix(PERF-012): bound job event memory growth`
  - `fix(TEST-033): cover late event subscribers`

---

### Task 3: Jobs route structure and warning failures (`MAINT-025`, `EXC-006`)

**Files:**
- Modify: `src/acheron/shell/api/routes/jobs.py`
- Create: `src/acheron/shell/api/routes/job_requests.py`
- Create: `src/acheron/shell/api/routes/job_streams.py`
- Create: `src/acheron/shell/api/routes/job_lifecycle.py`
- Create: `src/acheron/shell/api/routes/job_responses.py`
- Test: `tests/shell/api/test_jobs.py`
- Review entry: `docs/code_review/code-quality.md`

**Interfaces:**
- `jobs.py` remains the router assembly point and exposes the same endpoint paths, response models, and dependency behavior.
- `job_requests.py` owns source/voice normalization, submission/retry construction, and typed warning collection.
- `job_streams.py` owns log streaming; `job_lifecycle.py` owns get/cancel/resume/list route handlers; `job_responses.py` owns error and tracked-job response mapping.

- [x] **Step 1: Add failing tests for warning exception classification.**
  - Inject the expected store/backend exception during BOOTING warning collection and assert submission succeeds without warnings.
  - Inject a programming error and assert it propagates through the route error boundary instead of being logged and downgraded.
- [x] **Step 2: Run `uv run pytest tests/shell/api/test_jobs.py -q` and confirm the unexpected error is currently swallowed.**
- [x] **Step 3: Extract the four focused route modules without changing endpoint contracts.**
  - Move the existing request builders and warning collector to `job_requests.py`.
  - Move `job_logs()` to `job_streams.py`, lifecycle endpoints to `job_lifecycle.py`, and response/error helpers to `job_responses.py`.
  - Keep `jobs.py` imports and `APIRouter` registration explicit; do not introduce string-based route dispatch.
- [x] **Step 4: Narrow BOOTING warning handling to the documented backend failure type and preserve exception chaining for unexpected errors.**
- [x] **Step 5: Run the complete jobs route test file and verify submit, preview, retry, logs, cancel, resume, get, and list contracts.**
- [x] **Step 6: Run `just validate`.**
- [x] **Step 7: After review-loop approval, commit:**
  - `fix(MAINT-025): split jobs route responsibilities`
  - `fix(EXC-006): preserve unexpected warning failures`

---

### Task 4: Public API schemas and client boundaries (`ARCH-029`, `CORR-046`)

**Files:**
- Modify: `src/acheron/core/schemas.py`
- Modify: `src/acheron/api_client.py`
- Modify: `src/acheron/shell/api/schemas.py`
- Modify: `src/acheron/shell/api/routes/admin.py`
- Modify: `src/acheron/shell/api/routes/jobs.py` or `src/acheron/shell/api/routes/job_responses.py` after Task 3
- Test: `tests/core/test_schemas.py`
- Test: `tests/shell/api/test_schemas.py`
- Test: `tests/shell/api/test_admin.py`
- Test: `tests/shell/api/test_jobs.py`
- Test: `tests/test_api_client.py`
- Review entries: `docs/code_review/architecture.md`, `docs/code_review/correctness.md`

**Interfaces:**
- `CleanupResponse` has exactly one shared definition in `acheron.core.schemas`; both the public client and admin route import it from there.
- `OutputSummary.metadata` is a typed JSON-value mapping and `_tracked_to_response()` preserves each `OutputFile.metadata` value.

- [x] **Step 1: Add failing schema/client tests.**
  - Import the cleanup response through the client path and assert `acheron.shell.api.schemas` is not imported by `api_client.py`.
  - Build a tracked result containing non-empty artifact metadata, call the job response mapper/endpoint, and assert metadata survives in the JSON response.
- [x] **Step 2: Run `uv run pytest tests/core/test_schemas.py tests/shell/api/test_schemas.py tests/shell/api/test_admin.py tests/shell/api/test_jobs.py tests/test_api_client.py -q` and confirm the metadata field is absent.**
- [x] **Step 3: Move the cleanup response models to `core/schemas.py`, delete the server-local definition, and update imports.**
- [x] **Step 4: Add the typed metadata field to `OutputSummary` and copy metadata in the response mapper without exposing internal-only fields.**
- [x] **Step 5: Run focused tests, `just type-check`, and `just validate`.**
- [x] **Step 6: After review-loop approval, commit:**
  - `fix(ARCH-029): move cleanup response to core schemas`
  - `fix(CORR-046): preserve output metadata in job responses`

---

### Task 5: Retention and administrative observability (`EXC-007`, `OBS-016`)

**Files:**
- Modify: `src/acheron/shell/retention.py`
- Modify: `src/acheron/shell/stores/base.py` if the durable audit sink uses the store boundary
- Modify: `src/acheron/shell/api/admin_audit.py`
- Modify: `src/acheron/shell/orchestrator.py`
- Modify: `src/acheron/shell/job_store.py` if audit serialization is shared there
- Test: `tests/shell/test_retention.py`
- Test: `tests/shell/api/test_admin.py`
- Test: `tests/shell/test_orchestrator.py`
- Review entries: `docs/code_review/code-quality.md`, `docs/code_review/operations.md`

**Interfaces:**
- `RetentionService.apply()` converts only the declared `StoreError` deletion boundary into a diagnostic `CleanupFailure`; unexpected exceptions propagate with their cause intact.
- Administrative audit records are normalized once, emitted as structured records, and persisted through a durable sink that is loaded when a new orchestrator instance starts.

- [x] **Step 1: Add failing exception-boundary tests.**
  - Make `job_store.delete()` raise `StoreError` and assert a retryable `CleanupFailure` retains job context.
  - Make it raise `AttributeError` and assert the exception escapes the retention operation.
- [x] **Step 2: Add failing audit durability tests.**
  - Execute successful and failed admin actions, construct a new orchestrator against the same durable data root, and assert both records remain queryable.
  - Assert structured audit fields contain request ID, action, result, reason, job IDs, and affected count.
- [x] **Step 3: Run `uv run pytest tests/shell/test_retention.py tests/shell/api/test_admin.py tests/shell/test_orchestrator.py -q` and confirm the broad catch and process-local audit behavior.**
- [x] **Step 4: Narrow retention handling to `StoreError` with `raise ... from exc`; persist bounded audit records through the existing durable storage boundary or an append-only file under the canonical data root, and reload them on startup.**
- [x] **Step 5: Run focused tests and `just validate`.**
- [x] **Step 6: After review-loop approval, commit:**
  - `fix(EXC-007): preserve unexpected retention failures`
  - `fix(OBS-016): persist administrative audit records`

---

### Task 6: Dashboard URL and polling surfaces (`DX-008`, `PERF-013`)

**Files:**
- Modify: `dashboard/app.py`
- Modify: `dashboard/templates/partials/job_detail.html`
- Modify: `dashboard/templates/partials/cost.html` if the combined response changes its context
- Modify: `src/acheron/shell/orchestrator.py`
- Modify: `src/acheron/shell/api/routes/cost.py`
- Modify: `src/acheron/core/schemas.py` if the cost response carries the bounded job snapshot
- Test: `dashboard/tests/test_dashboard.py`
- Test: `dashboard/tests/test_job_detail.py`
- Test: `dashboard/tests/test_cost_partial.py`
- Test: `tests/shell/api/test_cost.py` if present, otherwise add it beside the cost route tests
- Review entries: `docs/code_review/operations.md`, `docs/code_review/surface.md`

**Interfaces:**
- Server-side dashboard fetches use the internal orchestrator URL; browser-rendered output links use an explicitly configured browser-facing URL.
- One bounded dashboard cost request returns the cost summary and the job snapshot needed by the cost partial, eliminating the second full job scan per poll.

- [x] **Step 1: Add failing dashboard tests.**
  - Configure distinct internal and browser-facing URLs and assert mocked server fetches target the internal URL while rendered output links target the browser-facing URL.
  - Instrument orchestrator reads during a cost poll and assert one bounded job query is performed.
- [x] **Step 2: Run `uv run pytest dashboard/tests/test_dashboard.py dashboard/tests/test_job_detail.py dashboard/tests/test_cost_partial.py -q` and confirm the current link and two-query behavior.**
- [x] **Step 3: Add the distinct browser-facing URL input with explicit validation and thread it only into rendered link context; keep internal fetch helpers on the internal URL.**
- [x] **Step 4: Extend the cost summary route/service with a bounded job snapshot or a dedicated combined dashboard response, then update the partial to consume one response per poll.**
- [x] **Step 5: Run focused dashboard and cost tests, then `just validate`.**
- [x] **Step 6: After review-loop approval, commit:**
  - `fix(DX-008): separate dashboard fetch and browser URLs`
  - `fix(PERF-013): combine dashboard cost polling data`

---

### Task 7: Worker SDK resource handling and pricing (`CORR-047`, `PERF-015`)

**Files:**
- Modify: `src/acheron/worker_sdk/pricing.py`
- Modify: `src/acheron/worker_sdk/_edge_http.py`
- Modify: `src/acheron/worker_sdk/inputs.py` or `src/acheron/worker_sdk/artifacts.py` only if the file-backed abstraction is required
- Test: `tests/worker_sdk/test_runpod_price.py`
- Test: `tests/worker_sdk/test_edge_http_multipart.py`
- Review entries: `docs/code_review/correctness.md`, `docs/code_review/operations.md`

**Interfaces:**
- `RunPodPrice.estimate()` returns finite cost with `CostBasis.MEASURED` for successful refresh and fresh cached-rate paths, and `CostBasis.CACHED` only after a failed refresh with a prior usable rate.
- Multipart parsing holds at most one bounded in-memory representation per part and hands large parts to the handler through the existing `Input`/stream contract.

- [x] **Step 1: Add failing pricing tests for successful refresh and unexpired cache paths.**
  - Assert finite cost and `MEASURED` when refresh succeeds.
  - Assert finite cost and `MEASURED` when the cached rate remains fresh.
  - Preserve the existing failed-refresh/prior-rate assertion for `CACHED`.
- [x] **Step 2: Add a multipart regression test using a near-limit part and assert the handler receives the complete bytes while the parser uses file-backed/spooled storage rather than `BytesIO.getvalue()`.**
- [x] **Step 3: Run `uv run pytest tests/worker_sdk/test_runpod_price.py tests/worker_sdk/test_edge_http_multipart.py -q` and confirm the pricing fall-through and memory-copy path.**
- [x] **Step 4: Fix the estimate branch and replace the multipart accumulation/copy path with a bounded spooled temporary-file or equivalent file-backed input implementation; preserve size and metadata validation.**
- [x] **Step 5: Run focused tests and `just validate`.**
- [x] **Step 6: After review-loop approval, commit:**
  - `fix(CORR-047): report measured valid RunPod pricing`
  - `fix(PERF-015): avoid duplicate multipart buffering`

---

### Task 8: Output and audio validation coverage (`TEST-031`, `TEST-032`)

**Files:**
- Modify: `tests/shell/api/test_job_outputs.py`
- Modify: `tests/shell/test_local_handlers.py`
- Read-only production references: `src/acheron/shell/api/routes/job_outputs.py`, `src/acheron/shell/local_handlers.py`
- Review entries: `docs/code_review/verification.md`

**Interfaces:**
- Intermediate output-directory symlinks return the same structured 404 contract as final-file and job-root symlinks.
- Every malformed PCM-WAV branch raises the existing actionable domain error without changing valid-file behavior.

- [x] **Step 1: Add the intermediate-directory symlink test.**
  - Replace a nested output directory with a symlink to an outside directory, request the output, and assert a structured 404 with no outside-file read.
- [x] **Step 2: Run `uv run pytest tests/shell/api/test_job_outputs.py -q` and observe the new test fail only if the production path is not already protected.**
- [x] **Step 3: Add parameterized malformed-WAV fixtures for non-PCM audio, zero byte rate, malformed RIFF header, and missing required chunks; assert the public domain error text for each branch.**
- [x] **Step 4: Run `uv run pytest tests/shell/test_local_handlers.py -q` and ensure fixtures exercise the validators rather than only helper internals.**
- [x] **Step 5: If the symlink or WAV tests expose a production defect, add the smallest production fix before changing expectations; otherwise keep this task test-only. Run `just validate`.**
- [x] **Step 6: After review-loop approval, commit:**
  - `fix(TEST-031): cover nested output symlink rejection`
  - `fix(TEST-032): cover malformed PCM WAV rejection`

---

### Task 9: Developer documentation and validation tooling (`DOC-014`, `DX-009`)

**Files:**
- Modify: `README.md`
- Modify: `src/acheron/cli.py` only if help text and actual command names disagree
- Modify: `Justfile`
- Test: `tests/shell/test_cli.py` if command help assertions are needed
- Test: `tests/first_run/test_1_quick_start.py` if README command snippets are exercised there
- Review entries: `docs/code_review/surface.md`

**Interfaces:**
- README examples match `acheron --help`, `acheron job --help`, `acheron cleanup --help`, and `acheron admin --help`, including cleanup preview versus `--apply`.
- `just validate` remains the code gate; `just ux-validate` is documented as a required separate pre-merge gate because the existing UX rubric requires a refresh before it can pass on this branch.

- [x] **Step 1: Add a failing gate test or shell-level assertion that a malformed UX story causes the documented UX gate to fail before merge.**
- [x] **Step 2: Run the existing CLI/help and UX validation tests to establish the red behavior.**
- [x] **Step 3: Document the separate `ux-validate` gate in the Justfile and README without duplicating the recipe body; do not make the authoritative code gate depend on a known-stale rubric.**
- [x] **Step 4: Replace the README's incorrect `acheron admin ...` mutation examples with the actual `job archive`, top-level `cleanup`, and `admin reap-stuck` namespaces, including runnable preview/apply examples.**
- [x] **Step 5: Run the CLI/help tests and `just validate`; record any pre-existing `just ux-validate` rubric drift explicitly.**
- [x] **Step 6: After review-loop approval, commit:**
  - `fix(DOC-014): document administrative CLI namespaces`
  - `fix(DX-009): document separate UX validation gate`

**Execution note:** The story permits a separate UX gate. The branch keeps `just validate` green as the authoritative code gate, documents strict `just ux-validate` as required before merge, and records its 29 pre-existing rubric metadata errors rather than bypassing them or refreshing UX evidence outside this code-review scope.

---

## Final review and completion

After Task 9:

- [x] Run the broad whole-branch review against the range `$(git merge-base master HEAD)..HEAD` with correctness, tests, maintainability, API/security, performance, and documentation lenses.
- [x] Resolve the final review findings and run a scoped re-review; no blocker/high/medium findings remain.
- [x] Run `just validate` on the final branch and inspect `git diff master...HEAD` directly.
- [x] Confirm every selected story has `status: verified`, updated `files[].lines`, `last_verified_at`, and its implementation commit in `fixed_in`.
- [x] Record stale exclusions, parked findings, and deferred optional improvements in the final handoff.

### Completion record

- Implementation bundles: `2a11136`, `75d5b7e`, `74fdf0d`, `092ef77`, `001ee71`, `6aa2f8a`, `fc257a1`, `f9ae89b`, `5ba5e48`.
- Review metadata and plan-finalization commits: `26cdf83`, `4097cbd`, `d7c3fdd`, `8db14b8`, `cf02184`, `03e7c38`, `0cbc2d8`, `0ebf9b8`, `1ef2524`.
- Final verification: `just validate` passed with 1,756 tests passed and 9 skipped; final broad review found no blocker/high/medium residuals.
- Review inventory: 276 total stories; 196 verified, 61 fixed, 7 open low-severity, and 12 stale.
- Deferred gate: strict `just ux-validate` remains a required separate pre-merge gate and currently reports 29 pre-existing UX metadata errors; refreshing that rubric is outside this code-review plan.
