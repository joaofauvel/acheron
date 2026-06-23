---
branch: chore/code-review-update
initial_review_commit: 23c29e1
last_updated_commit: dbec2be
last_staleness_scan:
  commit: dbec2be
  date: 2026-06-23
---

# Correctness

## CORR — Correctness

**Grade:** B

Layer 8a added the `worker_sdk` subpackage, the qwen3tts RunPod worker, and the transport `_multipart` refactor. Five new CORR findings: CORR-013 (medium) — `_parse_multipart` discards the per-part `X-Acheron-Metadata` header, losing per-chunk ordering info; CORR-014 (high) — `RunPodClient.run` silently treats a FAILED RunPod job as a successful empty result, propagating as a 0-artifact JobResult; CORR-015 (medium) — `create_worker_app` cherry-picks routes from `EdgeApp` via a hardcoded `inner_paths` set, silently dropping new routes; CORR-016 (low) — `worker_sdk/__init__.py` docstring falsely claims the module is GPU-SDK-free at import time; CORR-017 (low) — `_build_multipart_response` materializes the entire artifact stream in memory, defeating the `StreamArtifact` contract. CORR-009, CORR-010, CORR-011, CORR-012 re-resolved — cited code unchanged since 63faed4. All other stories remain verified at dbec2be.

### CORR-001 — StreamingExecutor ignores JobResult.status — FAILED results silently treated as SUCCESS

```yaml
status: verified
severity: critical
effort: M
reviewed_at: 23c29e1
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in: ["9279c5152389a77b32280e24f94dc6e5fb6ca79f"]
files:
  - path: src/acheron/shell/executors/streaming.py
    lines: 198-218
  - path: src/acheron/shell/executors/streaming.py
    lines: 155-165
related: [TYPE-002]
```

**Issue.** The `_stage` method (streaming.py:198-218) never checks `result.status` after the handler returns. It unconditionally saves outputs to the cache, puts the result on the downstream queue, and returns the cost — regardless of whether the status is SUCCESS, FAILED, or PARTIAL. The `_build_result` method (streaming.py:155-165) determines the PlanResult status solely from `last_error`, which is only set when a stage raises an exception caught by the TaskGroup. A handler that returns `JobResult(status=FAILED)` without raising causes the streaming executor to report `status="completed"` with `completed_steps=len(steps)`, and the pipeline continues to downstream stages as if the step succeeded. The AsyncExecutor (async_executor.py:53-61) and SequentialExecutor (sequential.py:39-45) both check `result.status` and count non-SUCCESS results as failed.

**Why it matters.** The streaming executor is the default strategy. Any HTTP worker returning a FAILED JobResult — the standard non-exception failure signal — is silently treated as successful, producing incorrect PlanResult status, inflated completed_steps, and allowing downstream stages to run on invalid or empty inputs. Critical because it produces wrong outputs on the default execution path with no error signal.

**Recommendation.** In `_stage`, check `result.status` after the handler returns. If status is not SUCCESS, raise a `PipelineError` (or similar AcheronError) so the existing TaskGroup exception path handles it: set `last_error`, mark the plan as failed, and propagate `_END` to skip downstream stages. This aligns the streaming executor with the AsyncExecutor's status-checking behavior.

**Verification.** Add a test where a step handler returns `JobResult(status=JobStatus.FAILED, error="test")` without raising. Verify the StreamingExecutor returns a PlanResult with `status="failed"` (not "completed"), the error string in `errors`, and downstream steps are skipped. Compare with AsyncExecutor behavior to confirm parity.

### CORR-002 — BatchAsyncExecutor duplicates AsyncExecutor — batch flag never checked, no batch submission implemented

```yaml
status: verified
severity: medium
effort: M
reviewed_at: 23c29e1
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in: ["e0da69f"]
files:
  - path: src/acheron/shell/executors/batch_async.py
    lines: 16-79
  - path: src/acheron/shell/executors/async_executor.py
    lines: 22-75
related: [ARCH-001, MAINT-001]
```

**Issue.** `BatchAsyncExecutor.run` (batch_async.py:26-79) is a line-for-line copy of `AsyncExecutor.run` (async_executor.py:22-75). The `PlanStep.batch` flag is never inspected. The class docstring claims "Batch-flagged steps receive all outputs from completed preceding steps so the handler can construct a BatchJob," but no such logic exists — each step is dispatched individually via `self._handler(step, plan)`, identical to AsyncExecutor.

**Why it matters.** Users selecting `batch_async` strategy get identical behavior to `async`, providing no GPU batch optimization and making the strategy name misleading. The duplicate code also creates a maintenance burden where fixes must be applied to both copies or they drift. Medium because outputs are correct but the advertised optimization is a no-op.

**Recommendation.** Either implement the documented batch logic (collect outputs from preceding steps, construct BatchJob for batch-flagged steps, use StreamingWorker.submit_batch/poll_batch/collect_results) or remove BatchAsyncExecutor and `ExecutorStrategy.BATCH_ASYNC` per greenfield rules and switch the CLI default to a strategy that exists. Do not leave the stub.

**Verification.** Submit a plan with `batch=True` steps using BATCH_ASYNC strategy. Verify that batch-flagged steps are actually submitted as batches (not individual execute calls). Alternatively, verify the strategy is removed and existing tests pass with `just test`.

### CORR-003 — GrpcWorker.submit_batch — all-or-nothing gather, synchronous execution, state lost across factory instances

```yaml
status: verified
severity: medium
effort: M
reviewed_at: 23c29e1
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in: ["e0da69f"]
files:
  - path: src/acheron/shell/transports/grpc.py
    lines: 103-106
  - path: src/acheron/shell/transports/grpc.py
    lines: 38
  - path: src/acheron/shell/transports/grpc.py
    lines: 122-124
related: [MAINT-001]
```

**Issue.** Three issues in the batch API: (1) `submit_batch` (grpc.py:104) uses `asyncio.gather(*[self.execute(job) for job in batch.jobs])` without `return_exceptions=True` — a single failed job raises and the batch handle is never returned, making partial results inaccessible via `poll_batch`. (2) `submit_batch` blocks until ALL jobs complete before returning the handle, defeating the submit/poll/collect pattern where callers should poll progress asynchronously. (3) `_batches` (grpc.py:38) is per-instance state, but `default_worker_factory` (step_handler.py:112) creates a new GrpcWorker for each step execution — so `submit_batch` on one instance and `poll_batch`/`collect_results` on another would not share `_batches`, making the batch API non-functional through the normal dispatch path.

**Why it matters.** The batch submission API is non-functional as designed — partial failures kill the batch, polling is meaningless, and state is lost across factory-created instances. Any caller attempting to use the StreamingWorker batch interface through the standard step handler would get empty results from `poll_batch`/`collect_results`. Medium because the batch path is not currently exercised, but the interface is misleading.

**Recommendation.** Use `return_exceptions=True` in gather to allow partial success. Make `submit_batch` return immediately after submission (not after completion). Move `_batches` state to a shared or long-lived worker instance rather than creating new instances per call. If batch submission is not yet needed, consider simplifying the interface until it is.

**Verification.** Call `submit_batch` with a mix of succeeding and failing jobs. Verify `poll_batch` reports both completed and failed counts. Verify `collect_results` returns all results (success and failure). Test through the factory path to confirm state persistence across instances.

### CORR-004 — SequentialExecutor lets handler exceptions propagate — no PlanResult returned, API shows total_steps=0

```yaml
status: verified
severity: medium
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in: ["9817feaa0f5e3e209b58b12779964ab45e029e37"]
files:
  - path: src/acheron/shell/executors/sequential.py
    lines: 38-46
related: [OBS-004]
```

**Issue.** `SequentialExecutor.run` (sequential.py:38) calls `await self._handler(step, plan)` without a try/except. If the handler raises any exception, the for loop is interrupted, `run()` raises, and no PlanResult is created. The orchestrator's `_execute` (orchestrator.py:276-281) catches the exception and sets `tracked.status = "failed"`, but `tracked.result` remains None. The API response (jobs.py:78-80) then returns `total_steps=0, completed_steps=0`, which is misleading — the plan has steps. The AsyncExecutor (async_executor.py:44-47) uses `gather(..., return_exceptions=True)` and includes exceptions in the PlanResult errors.

**Why it matters.** Users of the sequential executor see a failed job with zero total steps and no error details, hiding which step failed and why. This is inconsistent with the AsyncExecutor which returns a PlanResult with step-level errors and correct total_steps. Medium because it degrades failure diagnostics on a non-default but selectable strategy.

**Recommendation.** Wrap the handler call in a try/except. On exception, mark the step as failed, add to `failed_steps`, append the error message, and `continue` to the next step (skipping dependents). This matches the AsyncExecutor's behavior.

**Verification.** Run a plan with SequentialExecutor where a step handler raises. Verify a PlanResult is returned with `status="failed"`, correct `total_steps`, the failing step in `errors`, and dependent steps marked as skipped.

### CORR-005 — ASR worker selection ignores output language — may dispatch worker that can't produce required output

```yaml
status: verified
severity: medium
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in: ["913943031f3667a13cec2210f8085d6ea04cc316"]
files:
  - path: src/acheron/shell/step_handler.py
    lines: 64-65
related: []
```

**Issue.** `_language_matches` (step_handler.py:64-65) for `WorkerType.ASR` only checks `src in caps.supported_languages_in`, ignoring `supported_languages_out`. The planner validates ASR workers with `_has_worker(WorkerType.ASR, caps, src, src)` (planner.py:66), which requires `src` in BOTH `supported_languages_in` and `supported_languages_out`. If two ASR workers are registered where Worker A has `src` in input but not output and Worker B has `src` in both, the planner accepts the plan (Worker B exists) but the step handler may select Worker A (first match, only input checked), which cannot produce output in the source language.

**Why it matters.** A worker that cannot produce output in the required language may be dispatched over one that can, causing runtime failures or incorrect transcription output. The discrepancy between planner validation and handler selection creates a false sense of safety. Medium because it requires a specific multi-worker registration pattern to trigger.

**Recommendation.** Add `and src in caps.supported_languages_out` to the ASR case in `_language_matches` to match the planner's `_has_worker` validation.

**Verification.** Register two ASR workers — one with `supported_languages_out` missing the source language, one with it. Submit an audio job. Verify the step handler never selects the worker missing the output language, even if it is registered first.

### CORR-006 — _consume_final_queue checks for AcheronError on queue — dead code, queue only holds JobResult | None

```yaml
status: verified
severity: low
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in:
  - 640bb03
files:
  - path: src/acheron/shell/executors/streaming.py
    lines: 118-123
related: []
```

**Issue.** `_consume_final_queue` (streaming.py:102) checks `isinstance(first, AcheronError)` on the first item from the final queue. The queue is typed `Queue[JobResult | None]` and stages only put `JobResult` objects or `None` (the `_END` sentinel) on queues — never `AcheronError`. Errors propagate via task exceptions caught by the TaskGroup, not via queue items. This check can never be True.

**Why it matters.** Dead defensive code that suggests a misunderstanding of the error flow. A reader might believe errors are passed through queues, obscuring the actual exception-based error mechanism. Low because it has no functional impact.

**Recommendation.** Remove the `isinstance(first, AcheronError)` check and the `raise first` branch. If the intent was to detect error conditions early, document that errors arrive via TaskGroup exceptions, not queue items.

**Verification.** Confirm no test relies on this check. Run the streaming executor test suite after removal to verify no regression.

### CORR-007 — Streaming executor _END propagation skips unrelated downstream steps in non-linear DAGs

```yaml
status: verified
severity: low
effort: M
reviewed_at: 23c29e1
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in:
  - 640bb03
files:
  - path: src/acheron/shell/executors/streaming.py
    lines: 204-211
  - path: src/acheron/shell/executors/streaming.py
    lines: 4-8
related: []
```

**Issue.** The streaming executor models the plan as a linear pipeline: stage N reads from stage N-1's queue. If stage N-1 sends `_END` (because it failed or was skipped), stage N reads `_END` and skips (streaming.py:206). For a non-linear DAG where step C depends on step A (not B), but the topological order is [A, B, C], C reads from B's queue. If B fails and sends _END, C is skipped — even though C's actual dependency (A) succeeded. The TODO at streaming.py:204 acknowledges this limitation for future branch support.

**Why it matters.** If a non-linear Plan DAG is passed to the StreamingExecutor, steps that should run (their actual dependencies succeeded) would be silently skipped, producing incomplete output. The current planner only generates linear chains, so this is latent — but the Executor interface accepts arbitrary Plans. Low because the current planner never produces non-linear DAGs.

**Recommendation.** Either reject non-linear DAGs in the StreamingExecutor (validate that each step depends on at most the immediately preceding step in topological order), or implement proper per-dependency queue fan-out. Until then, document the linear-only constraint in the class docstring.

**Verification.** Construct a Plan with a non-linear DAG (e.g., A->B, A->C, B+C->D) and run through StreamingExecutor. Verify either an explicit error is raised or all steps whose dependencies succeeded execute correctly.

### CORR-008 — StreamingExecutor loses cost accounting when handler returns non-SUCCESS status

```yaml
status: verified
severity: medium
effort: S
reviewed_at: a1b11b2
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in:
  - f394eec53b1916a42c808146c3868969668d0358
files:
  - path: src/acheron/shell/executors/streaming.py
    lines: 220-237
related: []
```

**Issue.** The CORR-001 fix added `if result.status is not JobStatus.SUCCESS: raise WorkerError(msg)` at streaming.py:211-213. This raised before the `return result.metrics.cost_estimate or 0.0` at line 222. When a handler returned a FAILED (or PARTIAL) JobResult with a non-zero `metrics.cost_estimate`, the cost was discarded because the exception bypassed the return. AsyncExecutor and SequentialExecutor both accumulated `result.metrics.cost_estimate or 0.0` on the FAILED branch, so the streaming executor's behavior was inconsistent with the other two strategies.

**Why it matters.** A worker that returns FAILED without raising typically still incurred cost (GPU time, API calls, partial compute). The streaming executor is the default strategy; the loss of cost accounting on failed steps skews billing, budgeting, and observability. The test `test_handler_returning_failed_status_marks_plan_failed` used `metrics=JobMetrics(duration_seconds=0.0)` with no cost_estimate, so the bug was silent in the test suite.

**Recommendation.** Before the status check, capture the cost: `cost = result.metrics.cost_estimate or 0.0`. After raising WorkerError, return the cost from a side channel. The cleanest approach: make `_stage` record cost into a shared list before any failure check, so cost survives TaskGroup cancellation.

**Verification.** Add a test that submits a plan where a step handler returns `JobResult(status=FAILED, metrics=JobMetrics(cost_estimate=0.42))` through StreamingExecutor and asserts `result.total_cost == 0.42`. Compare with AsyncExecutor to confirm parity.

### CORR-009 — Step handler caches worker list and worker instances across steps and plans

```yaml
status: open
severity: medium
effort: S
reviewed_at: be7b3ab
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in: []
files:
  - path: src/acheron/shell/step_handler.py
    lines: 87-128
related: []
```

**Issue.** `create_step_handler` now caches `registry.list_all()` per `plan_id` and reuses `Worker` instances per `worker_id`. The worker list is never refreshed within a plan, and `_worker_instances` is never invalidated when the plan changes or when a worker's registration changes. If a worker is removed, moved to a new endpoint, or re-registered with different capabilities between plans (or, less commonly, mid-plan), the handler may still dispatch to the stale cached instance.

**Why it matters.** The optimization trades freshness of dispatch decisions for performance. A job can be sent to a removed worker or to an old endpoint/capability set, producing runtime failures or incorrect output. Because the handler is created once per `Orchestrator` and reused for all jobs, stale instances persist for the process lifetime unless the `worker_id` changes.

**Recommendation.** Scope worker-instance reuse to a single plan: clear `_worker_instances` whenever `plan.plan_id` changes, or create instances per step if cross-plan reuse is not required. If cross-plan reuse is intentional, add registry-version-based invalidation so the cache is refreshed whenever the registry changes.

**Verification.** Add a test that registers worker 'w1' at endpoint A, executes a step, then re-registers 'w1' at endpoint B and executes a step for a new plan. Assert that the handler uses the current registry entry and that removing 'w1' causes a `WorkerError` rather than dispatching to a stale instance.

### CORR-010 — `${VAR}` env-var expansion silently substitutes missing variables with empty string

```yaml
status: open
severity: medium
effort: S
reviewed_at: 63faed4
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in: []
files:
  - path: src/acheron/shell/config.py
    lines: 15-26
  - path: src/acheron/shell/health_providers.py
    lines: 110-114
related: [CFG-005, SEC-010]
```

**Issue.** `_expand_env_vars` (config.py:15-26) does `os.environ.get(m.group(1), "")` to resolve `${VAR}` references. An unset env var is silently replaced with the empty string rather than raising. For example, `api_key: ${HF_API_KEY}` with `HF_API_KEY` unset becomes `api_key: ""` and is accepted as a valid string. `create_health_providers` (health_providers.py:110-114) then checks `if settings.providers.<name>.api_key:`, so the empty string is falsy and the provider is silently dropped.

**Why it matters.** A user who follows the example YAML and forgets to set `HF_API_KEY` gets a working orchestrator that just never HuggingFace-checks anything. There is no `ValidationError`, no log line, and no startup message — the provider name 'huggingface' never appears in startup logs. The user sees a 401 from the platform API or a worker stuck in OFFLINE, not a config error pointing to the missing variable.

**Recommendation.** Raise a `ConfigError` (or log a WARNING) at load time when a referenced env var is unset. The same path is used for the auto-generated registration token and any user-authored secret; the contract should be: a `${VAR}` reference in YAML requires the env var to be set.

**Verification.** Load a YAML with `api_key: "${MISSING_KEY}"` against an empty environment and assert a clear error or WARNING log entry naming the missing variable.

### CORR-011 — Env-var expansion pattern only matches uppercase variable names

```yaml
status: open
severity: low
effort: S
reviewed_at: 63faed4
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in: []
files:
  - path: src/acheron/shell/config.py
    lines: 15
related: [CORR-010, CFG-005]
```

**Issue.** `_ENV_VAR_PATTERN` is `r"\$\{([A-Z0-9_]+)\}"` — uppercase letters, digits, underscores only. A YAML entry like `path: ${home_dir}` (lowercase) is silently left unchanged as the literal string `"${home_dir}"`, which then fails downstream type validation (e.g. pydantic rejecting a non-Path string for a Path field).

**Why it matters.** Users with lowercase or mixed-case env var names will see their config values left as literal `${...}` strings, leading to confusing downstream errors at startup or first use. The behavior is undocumented and the failure is silent.

**Recommendation.** Expand the pattern to `[A-Za-z_][A-Za-z0-9_]*` (POSIX env var names allow lowercase and must start with a letter or underscore), or document the uppercase-only constraint in the YAML config docs.

**Verification.** Set a lowercase env var and reference it in YAML; assert the expansion works as expected (or that the constraint is documented).

### CORR-012 — Health monitor trusts provider BOOTING status without bounding duration

```yaml
status: open
severity: low
effort: M
reviewed_at: 63faed4
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in: []
files:
  - path: src/acheron/shell/health.py
    lines: 133-152
related: [OBS-005]
```

**Issue.** `_handle_failure` (health.py:133-152) sets the worker to `BOOTING` and returns early when the platform provider reports `BOOTING`, without calling `record_health_failure` and without incrementing `consecutive_failures`. If a provider is misconfigured, stale, or buggy and returns `BOOTING` for a worker that is actually permanently offline, the worker stays in `BOOTING` indefinitely and never reaches the `max_failures` cleanup path.

**Why it matters.** A misconfigured or stale provider (wrong API key, endpoint permanently deleted, or a provider bug) would cause workers to accumulate in `BOOTING` state, never being cleaned up. Users would see workers as "booting" forever in the dashboard, and the system would accumulate dead worker registrations.

**Recommendation.** Track BOOTING duration (e.g. via a `booting_since` timestamp on `RegisteredWorker`) and treat workers stuck in BOOTING beyond a configurable timeout (e.g. 10 minutes) as OFFLINE. Alternatively, increment `consecutive_failures` for BOOTING workers but with a separate, higher threshold than the 3-failure rule for OFFLINE workers.

**Verification.** Mock a provider that always returns BOOTING for a worker. Run multiple health check cycles. Assert the worker is eventually removed (or that BOOTING duration is bounded by a timeout).

### CORR-013 — `_parse_multipart` discards per-part `X-Acheron-Metadata` header sent by the SDK edge

```yaml
status: open
severity: medium
effort: S
reviewed_at: dbec2be
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in: []
files:
  - path: src/acheron/shell/transports/http.py
    lines: 115-137
related: []
```

**Issue.** `_parse_multipart` iterates the multipart parts and materializes each binary part via `_materialize_artifact`. The orchestrator's per-part header parser reads `X-Acheron-Metadata` (line 130) and immediately discards the value (`_ = part.get("X-Acheron-Metadata")`). The SDK edge (`_edge_http.py:103`) emits this header carrying per-artifact metadata (sequence_id, chapter_id, sample_rate) that downstream stages need to reconstruct chunk ordering — the orchestrator throws it away, leaving `OutputFile` with only filename/size/checksum/content_type. No other path carries the per-artifact metadata forward.

**Why it matters.** The metadata header is the only way to associate an emitted `OutputFile` with its chapter and sequence position. The proto `Artifact` message carries an equivalent `metadata` field (synthesis.proto:35) but the HTTP transport's data path does not propagate it. Downstream consumers can't reconstruct the chunk ordering or chapter boundaries from the `OutputFile` list alone; per-chunk ordering is silently lost. Particularly impactful for TTS where chunks must be played in `sequence_id` order — the orchestrator's response can no longer tell the caller which WAV belongs to which sentence.

**Recommendation.** Parse `X-Acheron-Metadata` (JSON, same encoder as `_edge_http._encode_metadata`) and add a `metadata: dict[str, JsonValue]` field to `OutputFile`, or attach the parsed dict to the corresponding `OutputFile` via a new field. Then propagate the field through `PlanResult.outputs` to the API/dashboard.

**Verification.** Round-trip a multipart response with a part carrying `X-Acheron-Metadata: {"sequence_id": 0, "chapter_id": "ch1"}`; assert the materialized `OutputFile` exposes the parsed dict with the original keys/values.

### CORR-014 — `RunPodClient.run` silently treats a FAILED RunPod job as a successful empty result

```yaml
status: open
severity: high
effort: S
reviewed_at: dbec2be
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in: []
files:
  - path: src/acheron/worker_sdk/_runpod_client.py
    lines: 75-94
related: [EXC-001]
```

**Issue.** `RunPodClient.run` does `output_dict = output if isinstance(output, dict) else {"artifacts": output}` then `output_dict.get("artifacts", [])`. It never inspects the RunPod output's `status` field. When the cloud-side handler raises (e.g., model OOM, GPU not available, dependency missing), the runpod SDK may return `{"status": "FAILED", "error": "..."}` from `request.output()`. The current code extracts an empty `artifacts` list and returns a successful `RunPodJobResult` with `gpu_seconds > 0` and zero artifacts.

**Why it matters.** A failed RunPod job propagates up as a SUCCESS from the edge's `/execute` endpoint (the cost is non-zero, but the body is empty artifacts). The orchestrator's `HttpWorker` passes this through as `JobResult(outputs=())`, marking the plan stage as successful with no outputs. Downstream stages fail with empty-input errors that point to the consumer, not the actual cause. Billing shows the cost was incurred (RunPod charges for failed pods that scheduled) but the user has no actionable error message. The most common trigger — model loading failure, GPU OOM, cold-start timeout — is exactly the scenario the new RunPod forwarder is meant to surface.

**Recommendation.** After the `output = await ...request.output()` call, inspect `output.get("status")` and raise `WorkerError(msg)` (or a more specific `WorkerUnavailableError` on cold-start FAILEDs) when status is not COMPLETED. Carry the RunPod error string into the raised exception message. Make the test that exercises a FAILED status mock the output dict with `{"status": "FAILED", "error": "OOM"}` and assert the `WorkerError` is raised with the message.

**Verification.** Add a test using `_FakeEndpoints(output={"status": "FAILED", "error": "GPU OOM"})` and assert that `RunPodClient.run` raises `WorkerError` (or chained exception) with the error message included. Add a second test for `{"status": "CANCELLED"}` to ensure any non-COMPLETED status is rejected.

### CORR-015 — `create_worker_app` cherry-picks routes from `EdgeApp` via hardcoded `inner_paths`; new routes silently dropped

```yaml
status: open
severity: medium
effort: S
reviewed_at: dbec2be
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in: []
files:
  - path: src/acheron/worker_sdk/app.py
    lines: 99-143
related: [ARCH-012, MAINT-011]
```

**Issue.** `create_worker_app` builds an `inner = EdgeApp(...)` (which constructs its own FastAPI app with routes + lifespan) then constructs an outer `app` and copies routes via a hardcoded whitelist `inner_paths = {"/health", "/capabilities", "/execute"}`. If a new route is added to `EdgeApp` (e.g., `/metrics` for Prometheus, `/ready` for k8s readiness, `/version`), the outer `create_worker_app` silently drops it — the endpoint returns 404 from uvicorn. The duplicated construction also runs `EdgeApp`'s lifespan definition (`handler.startup()` + `handler.shutdown()`) as dead code that is never executed.

**Why it matters.** The hardcoded whitelist creates a hidden maintenance contract: every new route in `EdgeApp` must also be added to `create_worker_app`'s whitelist. There is no test, no warning, and no type-level guarantee that the lists stay in sync. The next developer adding a route to `EdgeApp` (e.g., a `/metrics` endpoint for Prometheus scraping) will see it work in unit tests (where the inner app is the one being tested) and break in production (where the outer app serves the actual edge container). The inner `EdgeApp` construction also wastes resources at boot (FastAPI app with its own route table + lifespan that is never run).

**Recommendation.** Either: (1) drop the inner `EdgeApp` construction entirely and inline the routes in `create_worker_app`'s outer app, or (2) use FastAPI's `app.mount("", inner.app)` instead of manually copying routes. Option (1) is simpler and matches the rest of the file's pattern (one FastAPI app, one lifespan). If keeping the inner `EdgeApp` is desired for testability, mount it instead of copying routes.

**Verification.** Add a new trivial route (e.g., `GET /version` returning `{"version": "0.1.0"}`) to `EdgeApp` and call `create_worker_app`; assert the route is reachable in the resulting FastAPI app's test client. This test would currently fail because `/version` is not in the hardcoded whitelist.

### CORR-016 — `worker_sdk` package docstring falsely claims it is GPU-SDK-free at import time

```yaml
status: open
severity: low
effort: S
reviewed_at: dbec2be
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in: []
files:
  - path: src/acheron/worker_sdk/__init__.py
    lines: 5-13
  - path: src/acheron/worker_sdk/cloud.py
    lines: 21
related: [ARCH-011]
```

**Issue.** The package docstring on `acheron.worker_sdk` (lines 5-13) explicitly states: "importing acheron.worker_sdk does not transitively load runpod (that import lives in `_runpod_client`, which is not part of the public re-exports). This lets tests of pure types (handler, artifacts, settings) run without the runpod SDK installed." However, the public `__init__.py` re-exports `RunPodForwarderHandler` and `make_runpod_handler` from `acheron.worker_sdk.cloud` (line 18). Importing `cloud.py` triggers `from acheron.worker_sdk._runpod_client import RunPodClient, RunPodJobResult` (cloud.py:22), which loads `_runpod_client.py`, which has `import runpod` at module top (line 21). So importing `acheron.worker_sdk` always loads runpod, regardless of whether the user touches the RunPod forwarder.

**Why it matters.** The documented contract is silently violated. Tests for `WorkerSettings`, `BytesArtifact`, or other pure types (which don't need runpod) cannot run in environments where the runpod SDK is not installed. The contract is what enables clean module separation in CI; a docstring claiming a property that doesn't hold is a footgun for test infrastructure and dev workflows that strip heavy deps. Currently `runpod ~=1.9` is a main dep, so the symptom is latent, but if the project ever moves runpod to an optional dep, the import would break for users of the pure type system.

**Recommendation.** Either: (1) update the docstring to acknowledge the real import chain (the simplest fix), or (2) move the runpod import inside the `RunPodForwarderHandler` and `make_runpod_handler` functions / methods (lazy at call time), and remove `RunPodForwarderHandler` from the public re-exports when runpod is not installed (use a `typing.TYPE_CHECKING` guard + a runtime availability check). Option (1) is consistent with the project's "do not over-engineer" rule.

**Verification.** In a fresh venv without the runpod package installed, `import acheron.worker_sdk` should either succeed (matching the docstring) or fail with a clear `ImportError` naming runpod (matching reality). Either outcome is fine; the current state (succeeds only when runpod is installed, but docstring claims it should not need to) is the bug.

### CORR-017 — `_build_multipart_response` materializes the entire artifact stream in memory, defeating the `StreamArtifact` design

```yaml
status: open
severity: low
effort: M
reviewed_at: dbec2be
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in: []
files:
  - path: src/acheron/worker_sdk/_edge_http.py
    lines: 97-116
related: [PERF-006]
```

**Issue.** `_build_multipart_response` iterates each artifact with `async for chunk in a.stream(): body_data += chunk` (lines 105-107), accumulating the entire stream into a single `bytes` object before the multipart body is constructed. The `StreamArtifact` variant in `artifacts.py:57-65` is documented as for "Lazily-produced chunks — long audio, batched generation" precisely to avoid this buffering, but the encoder ignores the streaming nature and materializes the full payload in memory. The constructed `Response` then holds the full body as a single `content=body` argument (line 113), which uvicorn will hold in memory before sending.

**Why it matters.** The current handlers (`StubTTSHandler`, `Qwen3TTSRunpodHandler`) emit short ~100ms silent WAVs per chunk, so the buffer is small. But the design claims to support long audio via `StreamArtifact`; the encoder silently turns that into a memory-hungry path that could OOM the edge container on a long chapter. The docstring at `artifacts.py:54-55` sets an expectation the encoder does not deliver. A user implementing a long-audio worker via `StreamArtifact` would see production failures with no warning.

**Recommendation.** Build the multipart body as an async iterator and return `StreamingResponse(body_iter, media_type=...)` instead of `Response(content=full_bytes, ...)`. The body iter yields the boundary header, each chunk from `artifact.stream()`, the trailing `\r\n`, then the metrics part, then the closing boundary. This preserves the streaming contract end-to-end (handler.stream → encoder → uvicorn chunked transfer).

**Verification.** Mock a `StreamArtifact` whose `stream()` yields 1000 chunks of 1MB each; assert that `_build_multipart_response` does not allocate a 1GB intermediate `bytes` object (e.g., patch the accumulator to count allocations, or assert a maximum peak buffer size).

## ML — ML correctness

**Grade:** A

No ML-specific findings. The codebase is not an ML training pipeline.

## MATH — Numerical correctness

**Grade:** A

No numerical correctness findings. Cost/duration aggregation is simple addition with no precision concerns. No divide-by-zero, NaN propagation, or float-comparison issues found.
