---
branch: chore/code-review-update
initial_review_commit: 23c29e1
last_updated_commit: eb6849c85d83f2277eb450f18a11e63cae2defd1
last_staleness_scan:
  commit: eb6849c85d83f2277eb450f18a11e63cae2defd1
  date: 2026-06-24
---

# Correctness

## CORR — Correctness

**Grade:** C

Layer 8b added the ASR path on the orchestrator (the new `_execute_asr_multipart` HTTP worker method and the matching `Input` Protocol with `StreamInput`/`FileInput` variants in `worker_sdk/inputs.py`) and refactored the SDK edge into a clean `_dispatch` + `_parse_multipart_request` + `_build_multipart_response` split. Eight new CORR findings: CORR-018 (medium) — `HttpWorker._execute_asr_multipart` reads the entire audio file into RAM and embeds the bytes in an httpx `files=` form, the request-side mirror of the response-side buffer (CORR-017); CORR-019 (medium) — SDK `_parse_multipart_request` materialises the whole request body via `await request.body()` plus a synthetic-header concatenation, so the edge never sees an audio chunk smaller than the full upload; CORR-020 (medium) — `make_runpod_handler` silently coerces a missing `input_audio.data` to empty bytes, so wire-format errors upstream become a successful empty artifact; CORR-021 (low) — `make_runpod_handler` does not validate that `input_audio` is a dict, so a non-dict payload crashes with `AttributeError` instead of `WorkerError`; CORR-022 (low) — `make_runpod_handler` does not validate `content_type` is a string, so `str(42)` silently coerces a wrong-typed content type; CORR-023 (low) — `_run_execute_multipart` only catches `WorkerError`, so `JSONDecodeError` / `ValidationError` from the envelope parser leak as opaque 500s; CORR-024 (low) — `_parse_multipart_request` hardcodes `BytesInput.metadata={}` and never parses the per-part `X-Acheron-Metadata` header (the request-side mirror of CORR-013); CORR-025 (low) — `_parse_multipart_request` treats any non-`application/json` part as audio, regardless of content type, so a legitimate sidecar part would be misinterpreted as the audio input. Carry-overs: CORR-009 (medium, step-handler worker cache) re-resolved — cited code unchanged in spirit, line numbers shifted; CORR-013 (medium, per-part metadata discarded) re-resolved and now has a request-side mirror in CORR-024; CORR-016 (low, `worker_sdk` import-time runpod load) re-resolved — docstring/re-export still violates the contract; CORR-017 (low, response materialisation) re-resolved — `_build_multipart_response` line range updated, behavior unchanged. CORR-014 (high, RunPodClient.run silent FAILED) remains open and is unaffected by the diff. All other stories remain verified.

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
  commit: 7d4754a
  date: '2026-06-24'
fixed_in: []
files:
- path: src/acheron/shell/step_handler.py
  lines: 113-153
related: []
```

**Issue.** `create_step_handler` now caches `registry.list_all()` per `plan_id` and reuses `Worker` instances per `worker_id`. The worker list is never refreshed within a plan, and `_worker_instances` is never invalidated when the plan changes or when a worker's registration changes. If a worker is removed, moved to a new endpoint, or re-registered with different capabilities between plans (or, less commonly, mid-plan), the handler may still dispatch to the stale cached instance.

**Why it matters.** The optimization trades freshness of dispatch decisions for performance. A job can be sent to a removed worker or to an old endpoint/capability set, producing runtime failures or incorrect output. Because the handler is created once per `Orchestrator` and reused for all jobs, stale instances persist for the process lifetime unless the `worker_id` changes.

**Recommendation.** Scope worker-instance reuse to a single plan: clear `_worker_instances` whenever `plan.plan_id` changes, or create instances per step if cross-plan reuse is not required. If cross-plan reuse is intentional, add registry-version-based invalidation so the cache is refreshed whenever the registry changes.

**Verification.** Add a test that registers worker 'w1' at endpoint A, executes a step, then re-registers 'w1' at endpoint B and executes a step for a new plan. Assert that the handler uses the current registry entry and that removing 'w1' causes a `WorkerError` rather than dispatching to a stale instance.

### CORR-010 — `${VAR}` env-var expansion silently substitutes missing variables with empty string

```yaml
status: verified
severity: medium
effort: S
reviewed_at: 63faed4
last_verified_at:
  commit: 7d4754a
  date: '2026-06-24'
fixed_in:
- 7d4754a
files:
- path: src/acheron/shell/config.py
  lines: 15-26
- path: src/acheron/shell/health_providers.py
  lines: 110-114
related:
- CFG-005
- SEC-010
```

**Issue.** `_expand_env_vars` (config.py:15-26) does `os.environ.get(m.group(1), "")` to resolve `${VAR}` references. An unset env var is silently replaced with the empty string rather than raising. For example, `api_key: ${HF_API_KEY}` with `HF_API_KEY` unset becomes `api_key: ""` and is accepted as a valid string. `create_health_providers` (health_providers.py:110-114) then checks `if settings.providers.<name>.api_key:`, so the empty string is falsy and the provider is silently dropped.

**Why it matters.** A user who follows the example YAML and forgets to set `HF_API_KEY` gets a working orchestrator that just never HuggingFace-checks anything. There is no `ValidationError`, no log line, and no startup message — the provider name 'huggingface' never appears in startup logs. The user sees a 401 from the platform API or a worker stuck in OFFLINE, not a config error pointing to the missing variable.

**Recommendation.** Raise a `ConfigError` (or log a WARNING) at load time when a referenced env var is unset. The same path is used for the auto-generated registration token and any user-authored secret; the contract should be: a `${VAR}` reference in YAML requires the env var to be set.

**Verification.** Load a YAML with `api_key: "${MISSING_KEY}"` against an empty environment and assert a clear error or WARNING log entry naming the missing variable.

### CORR-011 — Env-var expansion pattern only matches uppercase variable names

```yaml
status: verified
severity: low
effort: S
reviewed_at: 63faed4
last_verified_at:
  commit: 7d4754a
  date: '2026-06-24'
fixed_in:
- 7d4754a
files:
- path: src/acheron/shell/config.py
  lines: 15
related:
- CORR-010
- CFG-005
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
status: verified
severity: medium
effort: S
reviewed_at: dbec2be
last_verified_at:
  commit: pending
  date: 2026-06-24
fixed_in: [pending]
files:
  - path: src/acheron/shell/transports/_multipart.py
    lines: 29-99
  - path: src/acheron/shell/transports/http.py
    lines: 188-208
  - path: src/acheron/core/models.py
    lines: 113
  - path: src/acheron/worker_sdk/_edge_http.py
    lines: 89-117
  - path: tests/shell/transports/test_http_multipart.py
    lines: 37-67
related: []
```

**Issue.** `_parse_multipart` iterates the multipart parts and materializes each binary part via `_materialize_artifact`. The orchestrator's per-part header parser reads `X-Acheron-Metadata` (line 224) and immediately discards the value (`_ = part.get("X-Acheron-Metadata")`). The SDK edge (`_edge_http.py:103`) emits this header carrying per-artifact metadata (sequence_id, chapter_id, sample_rate) that downstream stages need to reconstruct chunk ordering — the orchestrator throws it away, leaving `OutputFile` with only filename/size/checksum/content_type. No other path carries the per-artifact metadata forward.

**Why it matters.** The metadata header is the only way to associate an emitted `OutputFile` with its chapter and sequence position. The proto `Artifact` message carries an equivalent `metadata` field (synthesis.proto:35) but the HTTP transport's data path does not propagate it. Downstream consumers can't reconstruct the chunk ordering or chapter boundaries from the `OutputFile` list alone; per-chunk ordering is silently lost. Particularly impactful for TTS where chunks must be played in `sequence_id` order — the orchestrator's response can no longer tell the caller which WAV belongs to which sentence.

**Recommendation.** Parse `X-Acheron-Metadata` (JSON, same encoder as `_edge_http._encode_metadata`) and add a `metadata: dict[str, JsonValue]` field to `OutputFile`, or attach the parsed dict to the corresponding `OutputFile` via a new field. Then propagate the field through `PlanResult.outputs` to the API/dashboard.

**Verification.** Round-trip a multipart response with a part carrying `X-Acheron-Metadata: {"sequence_id": 0, "chapter_id": "ch1"}`; assert the materialized `OutputFile` exposes the parsed dict with the original keys/values.

### CORR-014 — `RunPodClient.run` silently treats a FAILED RunPod job as a successful empty result

```yaml
status: verified
severity: high
effort: S
reviewed_at: dbec2be
last_verified_at:
  commit: 4863597
  date: 2026-06-24
fixed_in: [4863597]
files:
  - path: src/acheron/worker_sdk/_runpod_client.py
    lines: 75-102
  - path: tests/worker_sdk/test_runpod_client.py
    lines: 81-100
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
  commit: 7d4754a
  date: '2026-06-24'
fixed_in: []
files:
- path: src/acheron/worker_sdk/app.py
  lines: 139-146
related:
- ARCH-012
- MAINT-011
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
  commit: e54458416e9bfe890a473dd9d542978d205b40a1
  date: 2026-06-23
fixed_in: []
files:
  - path: src/acheron/worker_sdk/__init__.py
    lines: 1-8
  - path: src/acheron/worker_sdk/__init__.py
    lines: 12
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
  commit: 26b8067b3ed53f84e9d6f797f51d20fa117be60f
  date: 2026-06-24
fixed_in: []
files:
  - path: src/acheron/worker_sdk/_edge_http.py
    lines: 136-169
related: [PERF-006]
```

**Issue.** `_build_multipart_response` iterates each artifact with `async for chunk in a.stream(): body_data += chunk` (lines 105-107), accumulating the entire stream into a single `bytes` object before the multipart body is constructed. The `StreamArtifact` variant in `artifacts.py:57-65` is documented as for "Lazily-produced chunks — long audio, batched generation" precisely to avoid this buffering, but the encoder ignores the streaming nature and materializes the full payload in memory. The constructed `Response` then holds the full body as a single `content=body` argument (line 113), which uvicorn will hold in memory before sending.

**Why it matters.** The current handlers (`StubTTSHandler`, `Qwen3TTSRunpodHandler`) emit short ~100ms silent WAVs per chunk, so the buffer is small. But the design claims to support long audio via `StreamArtifact`; the encoder silently turns that into a memory-hungry path that could OOM the edge container on a long chapter. The docstring at `artifacts.py:54-55` sets an expectation the encoder does not deliver. A user implementing a long-audio worker via `StreamArtifact` would see production failures with no warning.

**Recommendation.** Build the multipart body as an async iterator and return `StreamingResponse(body_iter, media_type=...)` instead of `Response(content=full_bytes, ...)`. The body iter yields the boundary header, each chunk from `artifact.stream()`, the trailing `\r\n`, then the metrics part, then the closing boundary. This preserves the streaming contract end-to-end (handler.stream → encoder → uvicorn chunked transfer).

**Verification.** Mock a `StreamArtifact` whose `stream()` yields 1000 chunks of 1MB each; assert that `_build_multipart_response` does not allocate a 1GB intermediate `bytes` object (e.g., patch the accumulator to count allocations, or assert a maximum peak buffer size).

### CORR-018 — ASR multipart path materializes entire audio file in memory

```yaml
status: open
severity: medium
effort: M
reviewed_at: e54458416e9bfe890a473dd9d542978d205b40a1
last_verified_at:
  commit: 7d4754a
  date: '2026-06-24'
fixed_in: []
files:
- path: src/acheron/shell/transports/http.py
  lines: 152-156
related: []
```

**Issue.** `HttpWorker._execute_with_upstream_input` (http.py:114-157, the unified helper that replaced the prior `_execute_asr_multipart` in 8c) reads the entire audio file via `await asyncio.to_thread(file_path.read_bytes)` (line 149) and embeds the resulting `bytes` object in the httpx `files=` form tuple. For an audiobook chapter that is tens or hundreds of MB this is a hard memory cliff: the orchestrator holds the file in RAM and the httpx client serialises it into a second buffer while building the multipart body, so peak orchestrator RSS for a single ASR step can be ~3-4x the audio size. The same anti-pattern is being closed in the response path (CORR-017) but is being introduced in the request path here.

**Why it matters.** The Layer 8b design intent (per the new `Input` Protocol and its `StreamInput` / `FileInput` variants in `worker_sdk/inputs.py`) is that audio flows without buffering. The orchestrator side silently violates that contract for ASR jobs. A long chapter could OOM the orchestrator on what is, by design, a streaming workload.

**Recommendation.** Either: (1) construct the httpx multipart body by hand from the file (open the file, use a streaming `Content-Length`-aware `content=` body iterator, set the boundary header manually so the file is not re-buffered), or (2) point the orchestrator to read from a `FileInput` over a shared volume (consistent with `FileInput` in `inputs.py`) so the worker reads bytes itself instead of receiving them in a POST body. Option (1) keeps the wire contract; option (2) eliminates the orchestrator's in-memory copy entirely.

**Verification.** Add a test that points the HttpWorker at a 200 MB sparse file and asserts (via a memory-profiling fixture or by patching `read_bytes` to record peak buffer size) that peak buffer is bounded and does not scale with file size.

### CORR-019 — SDK edge `_parse_multipart_request` materializes entire request body in memory

```yaml
status: open
severity: medium
effort: M
reviewed_at: e54458416e9bfe890a473dd9d542978d205b40a1
last_verified_at:
  commit: 26b8067b3ed53f84e9d6f797f51d20fa117be60f
  date: 2026-06-24
fixed_in: []
files:
  - path: src/acheron/worker_sdk/_edge_http.py
    lines: 256-280
related: []
```

**Issue.** `_parse_multipart_request` (line 195) calls `body = await request.body()` to obtain the full upload bytes, then on line 196-198 builds a second buffer `full_body = (synthetic_header).encode() + body` to satisfy the email `BytesParser`. The synthetic header prepend + the body concatenation is a transient ~2x memory spike; the persistent copy (`full_body`) is then held until parsing completes. The parser itself walks `body_data` linearly, so streaming is feasible but not used.

**Why it matters.** This is the receiving side of the same problem as CORR-018: the ASR edge is supposed to accept large audio uploads, but it never sees a chunk of audio smaller than the full request. The edge container's RSS can balloon on a 200 MB upload and (because of the email `BytesParser`) the entire body must be in memory at once — there is no per-part streaming. CORR-017 (response side) gets the same treatment, but at least that one is bounded by the small `~100ms` silent WAVs emitted by the existing stubs. The request side is unbounded.

**Recommendation.** Use the `python-multipart` library (already a FastAPI transitive dep) to parse the request as a streaming multipart, yielding parts one at a time. Forward the audio part's `SpooledTemporaryFile` directly to a `FileInput` (writing through `aiofiles`) so the worker can stream from disk instead of materialising in RAM. If the SDK must keep its zero-dep posture, at minimum avoid the synthetic-header concatenation by using `email.parser.BytesFeedParser` and feeding `request.stream()` directly.

**Verification.** Add a test that POSTs a multipart body with a 200 MB audio part and asserts the edge container's resident memory never grows past the 64 KiB streaming chunk size. Patch `request.body()` to record the peak bytes it ever holds.

### CORR-020 — `make_runpod_handler` silently coerces missing `data` field to empty bytes

```yaml
status: open
severity: medium
effort: S
reviewed_at: e54458416e9bfe890a473dd9d542978d205b40a1
last_verified_at:
  commit: 7d4754a
  date: '2026-06-24'
fixed_in: []
files:
- path: src/acheron/worker_sdk/cloud.py
  lines: '49'
related: []
```

**Issue.** In `make_runpod_handler._rp_handler` (cloud.py:46), `data_b64 = audio_payload.get("data", "")` defaults a missing or None `data` field to the empty string. `base64.b64decode("")` returns `b""`, and the resulting `BytesInput` is then handed to the handler. The granite_speech handler rejects empty audio with `WorkerError("Empty audio input")`, but other handlers (e.g. `StubASRHandler` in `stubs/_sdk_base/__init__.py:107`) silently return a successful JobResult with a canned transcript, regardless of whether the audio was empty. The orchestrator's caller therefore cannot distinguish "the worker transcribed silence" from "the worker received an empty payload". The non-str case is correctly rejected on line 48, so the gap is specifically the *missing* case.

**Why it matters.** A wire-format error upstream (e.g. a RunPod input builder that drops the audio field, or a hand-rolled RunPod request that omits `data`) is converted into a successful empty artifact from the edge. The orchestrator proceeds as if inference completed, downstream stages may process an empty transcript, and the cost is non-zero (the model ran, even if on nothing).

**Recommendation.** After `audio_payload = runpod_job["input"].get("input_audio")`, validate the payload shape up-front: `isinstance(audio_payload, dict)` and `isinstance(audio_payload.get("data"), str) and audio_payload["data"]`. If absent or empty, raise `WorkerError("RunPod input_audio.data is required and must be a non-empty base64 str")` to match the spirit of the existing non-str-data rejection.

**Verification.** Add a test to `tests/worker_sdk/test_cloud_audio.py` that calls the wrapped handler with `input_audio: {"content_type": "audio/wav", "metadata": {}}` (no `data` field) and asserts `WorkerError` is raised. Then add a test for `data: ""` (empty string) and assert the same.

### CORR-021 — `make_runpod_handler` does not validate that `input_audio` payload is a dict

```yaml
status: verified
severity: low
effort: S
reviewed_at: e54458416e9bfe890a473dd9d542978d205b40a1
last_verified_at:
  commit: pending
  date: 2026-06-24
fixed_in: [pending]
files:
  - path: src/acheron/worker_sdk/cloud.py
    lines: 44-48
related: []
```

**Issue.** The decoder (cloud.py:44) does `audio_payload = runpod_job["input"].get("input_audio")` and then `if audio_payload is not None:` enters a block that calls `audio_payload.get(...)`. If `input_audio` is a non-dict truthy value (e.g. an int, a list, a str), `audio_payload.get` raises `AttributeError`, which is not caught and is not a `WorkerError`. The RunPod serverless loop then sees an opaque traceback and may mark the job as INTERNAL_ERROR instead of forwarding a clean `JobResult` failure body to the orchestrator. The `metadata` field gets the same treatment on line 50 — a non-dict value would have been caught by the `isinstance(metadata_raw, dict)` check, but only if execution reaches line 50; the `AttributeError` on line 46/50 short-circuits first.

**Why it matters.** The wire contract is that `input_audio` is an object with `content_type`, `data`, `metadata`. A malformed upstream payload (typo, schema drift, attack) becomes an opaque crash in the edge container. The existing test `test_rejects_non_str_data_field` only exercises a `data: 42` case where `input_audio` is still a dict — the case of a non-dict `input_audio` is uncovered.

**Recommendation.** Immediately after `if audio_payload is not None:` add `if not isinstance(audio_payload, dict): raise WorkerError("RunPod input_audio must be a dict")` so all the downstream `.get` calls are safe. Also move the `data_b64` / `metadata_raw` extractions to local variables and validate their types together to match the symmetry of the existing checks.

**Verification.** Add a test where `input_audio` is a list (`["audio/wav", "AAAAAA==", {}]`) and an int (`42`); assert `WorkerError` is raised, not `AttributeError`.

### CORR-022 — `make_runpod_handler` does not validate `content_type` is a string

```yaml
status: verified
severity: low
effort: S
reviewed_at: e54458416e9bfe890a473dd9d542978d205b40a1
last_verified_at:
  commit: pending
  date: 2026-06-24
fixed_in: [pending]
files:
  - path: src/acheron/worker_sdk/cloud.py
    lines: 57-62
related: []
```

**Issue.** Line 55 reads `content_type=str(audio_payload.get("content_type", "audio/wav"))`. If the wire payload has `content_type: 42` or `content_type: ["audio/wav"]`, the `str()` call silently coerces it to `"42"` or `"['audio/wav']"`. The downstream handler then receives a `BytesInput` with a non-audio content type and (in the case of the granite_speech worker) feeds it to the transformers processor as if it were a real audio file. There is no parallel check for `isinstance(content_type, str)` like the one applied to `data_b64` and `metadata_raw` on the lines above.

**Why it matters.** Silent string coercion of an obviously-wrong type is the same class of bug as the silent empty `data` case (CORR-020) — a wire-format error is converted into a "successful" run that the orchestrator cannot distinguish from a legitimate one. The fix is one `isinstance` check.

**Recommendation.** Extract `content_type_raw = audio_payload.get("content_type", "audio/wav")` and `if not isinstance(content_type_raw, str): raise WorkerError(...)`. The default remains `"audio/wav"` only for the missing-key case, not for the wrong-type case.

**Verification.** Add a test where `input_audio: {"content_type": 42, "data": "AAAAAA==", "metadata": {}}` and assert `WorkerError` is raised.

### CORR-023 — `_run_execute_multipart` only catches `WorkerError` from the parser; JSONDecodeError / ValidationError leak as opaque 500s

```yaml
status: verified
severity: low
effort: S
reviewed_at: e54458416e9bfe890a473dd9d542978d205b40a1
last_verified_at:
  commit: pending
  date: 2026-06-24
fixed_in: [pending]
files:
  - path: src/acheron/worker_sdk/_edge_http.py
    lines: 181-211
related: []
```

**Issue.** The outer try/except on line 169 catches only `WorkerError` from `_parse_multipart_request`. But the parser (line 219) also raises `json.JSONDecodeError` (from `json.loads(envelope_json)`) and `pydantic.ValidationError` (from `ExecuteRequest.model_validate(...)`). Both inherit from `ValueError`, not `WorkerError`, so they bubble past the except and FastAPI returns a default opaque 500 with no `JobResult` body. The dispatch path on the legacy JSON side (the same `try/except BaseException` in `_dispatch`) also catches these, so the two branches are inconsistent: malformed JSON on the legacy path returns a clean JobResult, malformed JSON on the multipart path returns a stack trace.

**Why it matters.** The contract documented in the module docstring ("On handler failure the response is a plain JSON ``ExecuteError`` body with status 500") only holds for the JSON path. A client that sends a malformed multipart envelope gets an opaque 500; the orchestrator's `TypeAdapter(JobResult).validate_json(resp.content)` then raises a different error, masking the real cause. The two test cases `test_multipart_request_missing_json_part_raises` and `test_multipart_request_missing_boundary_raises` only cover `WorkerError` paths and don't exercise the JSONDecodeError / ValidationError paths.

**Recommendation.** Either widen the except to `(WorkerError, json.JSONDecodeError, ValidationError)` and wrap non-WorkerError exceptions in a WorkerError message, or — cleaner — call `Envelope.model_validate_json(envelope_json)` via a helper that always raises `WorkerError` (a `try/except (json.JSONDecodeError, ValidationError) as exc: raise WorkerError(f"Malformed envelope: {exc}") from exc`). Apply the same change to the JSON-path dispatcher if it doesn't already wrap pydantic errors.

**Verification.** Add a test that POSTs a multipart body with a non-JSON envelope part (e.g. `application/json` part whose body is `not-json`) and asserts the 500 response is a `JobResult` JSON with `status="failed"` and a useful `error` string, not a plain text 500.

### CORR-024 — Edge `_parse_multipart_request` hardcodes `BytesInput.metadata={}`; per-part metadata is never parsed/forwarded

```yaml
status: verified
severity: low
effort: S
reviewed_at: e54458416e9bfe890a473dd9d542978d205b40a1
last_verified_at:
  commit: pending
  date: 2026-06-24
fixed_in: [pending]
files:
  - path: src/acheron/worker_sdk/_edge_http.py
    lines: 79-82, 121
related: []
```

**Issue.** When constructing the `BytesInput` from the audio part (line 226-230), the code sets `metadata={}` unconditionally. There is no per-part header parser for an `X-Acheron-Metadata` (the same one the response side emits on line 108). This is the request-side mirror of CORR-013: the orchestrator is free to send per-chunk metadata over the multipart wire, but the SDK discards it before the handler ever sees it. The `Input` Protocol's `metadata: dict[str, JsonValue]` field was specifically designed to carry this — and the granite_speech handler does not read it (it just reads bytes), so the loss is silent.

**Why it matters.** The `Input.metadata` field has no current consumer, but CORR-013's `OutputFile.metadata` is also empty on the response side. Together this means the per-chunk metadata contract is broken in both directions for ASR jobs. Any future handler that wants to read input metadata (e.g. a multi-speaker ASR that distinguishes chapters by an injected `speaker_hint` header) will silently get `{}` and produce wrong output.

**Recommendation.** Add a per-part `X-Acheron-Metadata` parser in the loop at line 206-214, mirroring the response encoder on line 108 (`_encode_metadata`). Pass the parsed dict to `BytesInput(metadata=...)`. Add a test that posts a multipart body with `X-Acheron-Metadata: {"chapter_id": "ch1", "language": "en"}` and asserts the handler receives a `BytesInput` whose `metadata` dict has those keys.

**Verification.** Add a test to `tests/worker_sdk/test_edge_http_multipart.py` that posts a multipart with the per-part `X-Acheron-Metadata` header and asserts `handler.received_input.metadata == {"chapter_id": "ch1", ...}`.

### CORR-025 — Edge `_parse_multipart_request` treats any non-JSON part as audio regardless of content_type

```yaml
status: verified
severity: low
effort: S
reviewed_at: e54458416e9bfe890a473dd9d542978d205b40a1
last_verified_at:
  commit: pending
  date: 2026-06-24
fixed_in: [pending]
files:
  - path: src/acheron/worker_sdk/_edge_http.py
    lines: 86-110
related: []
```

**Issue.** In the part-iteration loop (line 206-214), the `elif audio_part is None: audio_part = part` branch catches every part whose content-type is not `application/json`, including `text/plain`, `image/jpeg`, `application/octet-stream`, etc. A multipart body that legitimately has multiple parts (e.g. an envelope + a debug log + an audio) would have the wrong part selected as the audio. The `BytesInput` is then built with whatever the wrong part's `content_type` reports (line 227), and the handler receives those bytes as if they were audio. The downstream granite_speech handler will fail with a transformer-side error, but the failure is reported as a model error rather than a wire-format error, and the stub ASR handler in `stubs/_sdk_base/__init__.py:107` would happily transcribe the bytes as if they were audio.

**Why it matters.** This is a defense-in-depth gap. The orchestrator currently sends exactly two parts (envelope + audio), so the loop is correct in practice. But a future caller that legitimately needs to attach sidecar parts (e.g. a separate metadata part, a per-chunk captioning hint) would silently have one of them treated as the audio input. The fix is one `startswith("audio/")` check, and it would also make the symmetric request-side validation of the orchestrator's `_execute_asr_multipart` (which only sends `audio/*` content types) explicit.

**Recommendation.** Change the elif to: `elif audio_part is None and part.get_content_type().startswith("audio/"): audio_part = part`. If no audio part is found at all, raise `WorkerError("Multipart body has no audio part")` so the response is a clean JobResult failure rather than a downstream crash.

**Verification.** Add a test to `tests/worker_sdk/test_edge_http_multipart.py` that posts a multipart body with only a `text/plain` sidecar part (no `audio/*` part) and asserts a 500 with `"no audio part"` in the error. Add a second test that posts two `audio/*` parts and asserts the first is used (or the second, if a more specific selector is added).

## ML — ML correctness

**Grade:** A

No ML-specific findings. The codebase is not an ML training pipeline.

## MATH — Numerical correctness

**Grade:** A

No numerical correctness findings. Cost/duration aggregation is simple addition with no precision concerns. No divide-by-zero, NaN propagation, or float-comparison issues found.

## CORR (8c delta)

### CORR-026 — `chars_per_token=4` default under-estimates CJK tokens; docstring claim is inverted

```yaml
status: verified
severity: medium
effort: S
reviewed_at: eb6849c85d83f2277eb450f18a11e63cae2defd1
last_verified_at:
  commit: 1fd0c3c
  date: 2026-06-24
fixed_in: [1fd0c3c]
files:
  - path: src/acheron/core/planner.py
    lines: 92-130
  - path: src/acheron/shell/config.py
    lines: 141-143
  - path: tests/core/test_planner.py
    lines: 273-289
related: [CFG-009, CORR-029]
```

**Issue.** The docstring on `validate_chunking_fits_workers` (planner.py:105-107) calls the `chars_per_token=4` default "conservative" because it "overestimates tokens for CJK languages". This is the inverse of the actual behavior. Latin-script text averages ~4 chars/token, but CJK averages ~1 char/token, so `estimated_tokens = chunking_max_length // 4` is a Latin-conservative estimate that *under*-estimates CJK by ~4x. The check `if estimated_tokens > c.max_input_tokens` therefore *passes* a CJK chunk that exceeds the worker's actual budget. The default config (max_chunk_length=250, max_input_tokens=2048) is safe (250 chars < 2048 tokens in both scripts), so the bug is latent. With a user-configured max_chunk_length=4000 and a CJK source the check passes (4000/4=1000 < 2048) but the worker receives 4000-token chunks and may OOM or fail to translate.

**Why it matters.** The plan-time check is the primary safety net against misconfigured chunking, meant to fail fast before any GPU time is spent. The docstring's "conservative" claim is the only document-level justification for the hard-coded `4` default. An operator who reads the docstring, sets a larger `max_chunk_length` for a long chapter, and feeds CJK content will see a passing plan and a runtime failure deep inside the worker's `model.generate` call — after warm-up + cold-start cost. The error is opaque ("tokenizer error", "shape mismatch") and not a `ChunkingTooLongForWorkerError`.

**Recommendation.** Either (a) drop the docstring's "conservative" claim and document the limitation explicitly ("4 chars/token is the Latin ratio; for CJK content the check is non-conservative and the operator must lower max_chunk_length manually"), or (b) pick a smaller default (e.g. `1 char/token`) that is conservative across all scripts and document the cost in token-budget slack. Add a test that exercises a CJK `max_chunk_length` above `chars_per_token * max_input_tokens` and asserts the check raises.

**Verification.** Add a test in `tests/core/test_planner.py` with `chars_per_token=4, chunking_max_length=4000, max_input_tokens=2048` and assert `ChunkingTooLongForWorkerError` is raised (current behavior would pass — that's the bug). Also add a test with CJK-character chunking max_length=1000 and assert the check still triggers the conservative bound (chars_per_token=1).

### CORR-027 — `_execute_with_upstream_input` only POSTs the first matching file; multi-file upstream outputs are silently truncated

```yaml
status: verified
severity: medium
effort: S
reviewed_at: eb6849c85d83f2277eb450f18a11e63cae2defd1
last_verified_at:
  commit: pending
  date: 2026-06-24
fixed_in: [pending]
files:
  - path: src/acheron/shell/transports/http.py
    lines: 133-144
  - path: tests/shell/transports/test_http_multipart.py
    lines: 163-272
related: [ARCH-020]
```

**Issue.** `_execute_with_upstream_input` (http.py:133-136) uses `next((o for o in upstream_outputs if content_type_predicate(o.content_type)), None)` and returns at the first match. If the upstream step emits multiple files matching the predicate (e.g. an extract step that produces per-chapter audio files, or a chunk step that splits chunks.json into per-chapter files), only the first is sent to the worker; the rest are silently dropped. The current ASR path is single-audio in the stubs, so this is latent. The TTS/TRANSLATION path passes the single chunks.json from the chunk step, so it is also latent. But the data path is now designed around "one upstream output per step", which couples the orchestrator to the current step output shapes.

**Why it matters.** A future refactor that splits extract into per-chapter audio outputs (natural for parallelism), or that introduces an alternative chunking strategy emitting per-chapter chunks.json, will silently lose all but the first output. The user would see a successful job with truncated input — a TTS chunk step with 50 chapters would produce TTS audio for chapter 1 only. The bug is silent because the response from the worker is still SUCCESS.

**Recommendation.** Document explicitly that the current contract is "one file per step per predicate". Add a `match` arm on the content_type in the case statement that picks the first matching file by design. Alternatively, change `next()` to raise `WorkerError(f"Multiple matching files in {upstream_step} output; orchestrator does not support multi-file dispatch")` so the gap is loud. Either way, the coupling is now visible in the code rather than implicit.

**Verification.** Add a test in `tests/shell/test_http_worker.py` that seeds the extract step with two audio/* output files for the same plan_job_id; assert the call to the worker POSTs only one of them (and document this as the contract).

### CORR-028 — `_parse_multipart` boundary extraction raises IndexError on response missing `boundary=`

```yaml
status: verified
severity: medium
effort: S
reviewed_at: eb6849c85d83f2277eb450f18a11e63cae2defd1
last_verified_at:
  commit: pending
  date: 2026-06-24
fixed_in: [pending]
files:
  - path: src/acheron/shell/transports/_multipart.py
    lines: 52-54
  - path: tests/shell/transports/test_http_multipart.py
    lines: 125-133
related: [CORR-013, DATA-006, DATA-008]
```

**Issue.** Line 187: `boundary_part = ctype.split('boundary=', 1)[1]` raises `IndexError` when the response's Content-Type is `multipart/mixed` but lacks a `boundary=` parameter (e.g. `multipart/mixed; charset=utf-8`). The orchestrator's `HttpWorker.execute()` calls `_parse_multipart` and would propagate the IndexError up. The JobResult-shaping contract on the wire (a `JobResult` JSON body on failure) is not met — the orchestrator's exception bubbles up to the executor, which logs an opaque IndexError, not a clean `WorkerError`. The edge's `_parse_multipart_request` validates `boundary=` defensively (line 191) and raises a `WorkerError`; the orchestrator should mirror that pattern for symmetry.

**Why it matters.** A worker that returns a malformed multipart Content-Type (a misbehaving edge implementation, a proxy that strips the boundary, or a future content-type like `multipart/mixed; charset=utf-8`) triggers an opaque IndexError in the orchestrator. The streaming/async/sequential executors catch the exception but the PlanResult's `errors` tuple is a single non-WorkerError exception string, not the structured `WorkerError` with a "Multipart body is missing boundary" message that the operator can act on. The edge side already has the right defensive check — the orchestrator-side gap is asymmetric.

**Recommendation.** Mirror the edge's defensive check: `if 'boundary=' not in ctype: raise WorkerError('Multipart/mixed response missing boundary')` before the split. Add a unit test in `tests/shell/test_http_worker.py` that returns a `multipart/mixed; charset=utf-8` body and asserts the orchestrator raises `WorkerError` with a useful message.

**Verification.** Add a test in `tests/shell/test_http_worker.py` (or `test_asr_multipart.py`) that uses respx to mock a 200 response with `content-type: multipart/mixed; charset=utf-8` and a body that looks multipart; assert `WorkerError` is raised with 'boundary' in the message.

### CORR-029 — `TranslateGemmaRunpodHandler._translate_batch` has no partial-success handling; mid-batch failure discards all completed work

```yaml
status: open
severity: medium
effort: M
reviewed_at: eb6849c85d83f2277eb450f18a11e63cae2defd1
last_verified_at:
  commit: 0e6c576
  date: '2026-06-24'
fixed_in: []
files:
- path: workers/translategemma/handler.py
  lines: 204, 239-287
- path: workers/translategemma/handler.py
  lines: 225-236
related:
- CORR-026
- MAINT-019
```

**Issue.** `_translate_batch` runs a single `self._model.generate(...)` per batch. If the 3rd of 10 batches (chunks 9-12 of 40) raises (OOM mid-batch, GPU fault, NaN/inf in input_ids), the `try/except` in `handle` (lines 203+ `await asyncio.to_thread(self._translate_all, ...)`) propagates the exception, and the orchestrator's `/execute` returns a 500 `JobResult` with all 8 previously translated batches discarded. The 32 chunks that were already on the GPU and translated are lost; the operator pays the warm-up cost again on the next attempt. The handler does not emit partial artifacts and does not surface the per-batch progress in the error message.

**Why it matters.** TranslateGemma-12b is 24 GB at BF16 — OOM mid-batch is a real risk for long chapters. The batched inference design (4 chunks per generate call) is supposed to amortize warm-up; the lack of partial-success handling means a single bad batch costs the operator the whole inference run. The make_runpod_handler returns a single `JobResult` per /run, so a retry re-does all the work from scratch. This is the same shape as the streaming executor CORR-008 fix (preserve cost on failure), but on the worker side.

**Recommendation.** Wrap each batch in its own try/except in `_translate_all`. Collect successful translations in `out: list[Optional[str]]` (None for failed batches). After the loop, raise a `WorkerError` listing the failed batch indices AND return the partial translations (e.g. by yielding them as artifacts in a second pass, or by writing them to a manifest the next retry can pick up). At minimum, log the count of successfully translated chunks before raising. A future maintainer can then design an idempotent retry that resumes from the last-successful batch.

**Verification.** Add a test in `workers/translategemma/tests/test_handler.py` that monkeypatches `_translate_batch` to raise on the 2nd call but succeed on the 1st and 3rd; assert the handler raises WorkerError AND assert a side-channel (e.g. a list passed in the test fixture) records that 1 batch's worth of chunks were processed before the failure.

### CORR-030 — `_parse_multipart` takes the first `application/json` part as metrics; a sidecar JSON part would be silently overwritten

```yaml
status: verified
severity: low
effort: S
reviewed_at: eb6849c85d83f2277eb450f18a11e63cae2defd1
last_verified_at:
  commit: pending
  date: 2026-06-24
fixed_in: [pending]
files:
  - path: src/acheron/shell/transports/_multipart.py
    lines: 65-99
  - path: tests/shell/transports/test_http_multipart.py
    lines: 70-122
related: [CORR-013, DATA-006, DATA-008]
```

**Issue.** Lines 215-219: `if part_ctype == 'application/json': ... metrics = _metrics_adapter.validate_json(...) ; continue`. The condition has no guard against duplicate JSON parts; whichever JSON part appears first in the multipart body becomes the `JobMetrics`. The SDK edge's `_build_multipart_response` (line 115) appends the metrics part LAST, so a well-behaved edge produces one artifact parts + one trailing metrics part. But a future edge that adds a sidecar `application/json` part (e.g. for per-job metadata) BEFORE the metrics would have the metadata parsed as JobMetrics and the real metrics discarded. The orchestrator would get a `metrics=JobMetrics(...)` parsed from the metadata dict, which would fail `validate_json` with a ValidationError, which is not caught and bubbles up as an opaque exception.

**Why it matters.** The wire contract is implicit (metrics is always last). The orchestrator's parser is silent on ordering. A future sidecar part would convert a structured wire change into a confused orchestrator. The cost of being explicit (one assert) is small.

**Recommendation.** Either (a) document the contract in the docstring of `_parse_multipart` ("Metrics part is the last `application/json` part"), or (b) assert the metrics part is the LAST part of the body: track `is_last = part is parts[-1]` and raise `WorkerError('application/json part is not the last part')` if a JSON part appears before a non-JSON part. The latter is defensive; the former matches the existing implicit contract.

**Verification.** Add a test in `tests/shell/test_http_worker.py` that builds a multipart body with `part1 (audio) + part2 (json non-metrics) + part3 (json metrics)`; assert the orchestrator either raises a clean WorkerError or, after the fix, uses the LAST json part as metrics.

### CORR-031 — `HttpWorker.health` uses deprecated Python 2 `except E1, E2:` syntax

```yaml
status: open
severity: low
effort: S
reviewed_at: eb6849c85d83f2277eb450f18a11e63cae2defd1
last_verified_at:
  commit: 7d4754a
  date: '2026-06-24'
fixed_in: []
files:
- path: src/acheron/shell/transports/http.py
  lines: '189'
related:
- MAINT-009
- CORR-013
```

**Issue.** Line 239: `except WorkerError, WorkerUnavailableError:`. This is Python 2 syntax; in Python 3 it parses as a tuple of exception classes and functions correctly today, but it has been a `SyntaxWarning` since 3.0 and is planned for removal in a future Python release. The `pyproject.toml` does not enable `-W error::SyntaxWarning`, so the warning is silent. The pattern is asymmetric with the other two except blocks in the same file (lines 78-82 and 173-179), which use the correct `except (X, Y):` parenthesised form.

**Why it matters.** Style inconsistency. Future Python versions may remove the comma form. A new contributor reading the file would copy the pattern, perpetuating the deprecation. A linter configured to flag `SyntaxWarning` (e.g. `ruff` rule `E999`-adjacent or `B033` for the bare-except-`BaseException` family) would not catch this without `-W error`. The code works today; the cost of fixing is one pair of parentheses.

**Recommendation.** Change `except WorkerError, WorkerUnavailableError:` to `except (WorkerError, WorkerUnavailableError):` for consistency with the two other except blocks in the same file (lines 78-82 and 173-179). Add a one-line `noqa` comment if the linter still flags it, or suppress the SyntaxWarning via a project-wide `# ruff: noqa: E999` directive in `pyproject.toml`.

**Verification.** After the fix, run `python3 -W error -c 'from acheron.shell.transports.http import HttpWorker'` and confirm no SyntaxWarning is raised. Run `just type-check` and `just test` to confirm no regression.

### CORR-032 — `TranslateGemmaRunpodHandler.handle` materializes the entire chunks.json in memory before validation

```yaml
status: open
severity: low
effort: M
reviewed_at: eb6849c85d83f2277eb450f18a11e63cae2defd1
last_verified_at:
  commit: 0e6c576
  date: '2026-06-24'
fixed_in: []
files:
- path: workers/translategemma/handler.py
  lines: 188-190
related:
- CORR-017
- CORR-018
- CORR-019
- MAINT-017
```

**Issue.** Line 187: `chunks_json_bytes = b"".join([chunk async for chunk in input.stream()])`. This loads the full chunks.json into RAM before validation. For a long chapter (e.g. 5000 chunks × 200 chars each = 1 MB JSON, or 20000 chunks × 250 chars = 5 MB), the handler holds the full JSON in memory plus the parsed list plus the validated chunks dict. The same anti-pattern exists on the orchestrator's request side (CORR-018) and the SDK's request parser (CORR-019), but the cloud handler is a third instance of the same shape. The Input Protocol has a `StreamInput` variant specifically to avoid this buffering, but `make_runpod_handler` always wraps in a `BytesInput` (cloud.py:54-58), so the cloud handler never sees a StreamInput from the RunPod forwarder.

**Why it matters.** For a 12B model the VRAM budget is the constraint, not host RAM. The 5-10 MB chunks.json is small relative to the model's 24 GB footprint, so the host-RAM doubling is not catastrophic. But the cloud handler is the only place that has access to a streaming chunked input (a future FileInput over a shared volume) — encoding the assumption "input is small JSON" in the handler shape couples it to the RunPod forwarder's base64 wrapper. A future multi-MB chapter would force the cloud handler to be rewritten.

**Recommendation.** Document the assumption (chunks.json < 10 MB) in the handler docstring. The Input Protocol's three variants (Bytes/Stream/File) are present but unused; if a future deployment shares a volume, the handler can switch to a streaming `json.loads` (one chunk at a time). For now, the simplest fix is a docstring note: "The RunPod forwarder always delivers chunks.json as a BytesInput; for very long chapters switch to a shared-volume FileInput."

**Verification.** Add a docstring note. The existing tests at 1-10 chunks are far below any memory cliff; no test change needed for the latent case. If the project ever moves to multi-MB chapters, the docstring becomes the contract.

### CORR-033 — `TranslateGemmaRunpodHandler._translate_batch` mutates the shared processor's tokenizer in-place

```yaml
status: open
severity: low
effort: M
reviewed_at: eb6849c85d83f2277eb450f18a11e63cae2defd1
last_verified_at:
  commit: 0e6c576
  date: '2026-06-24'
fixed_in: []
files:
- path: workers/translategemma/handler.py
  lines: 270-271
related:
- TYPE-010
```

**Issue.** Lines 267-269: `if tokenizer.pad_token_id is None and tokenizer.eos_token_id is not None: tokenizer.pad_token_id = tokenizer.eos_token_id`. The processor is a stateful object loaded once at `startup()` and held on `self._processor`. The assignment mutates the tokenizer's state in-place and persists across all subsequent `handle()` calls. For the single-handler single-process RunPod serverless case this is benign (the mutation happens once at boot). But: (1) if a future maintainer ever instantiates two handlers against the same processor (e.g. for load testing, or for a future model-rotation pattern), the second inherits the mutated state and the first's intentional non-mutation is lost. (2) If the processor is ever replaced via a hot-reload, the new processor's tokenizer starts un-mutated but the first call sets it. The mutation is correct but a side-effect on shared state.

**Why it matters.** Side effects on shared state are the same class of bug the codebase avoids in other modules (e.g. `WorkerCapabilities` is frozen). The mutation is hidden inside `_translate_batch` so a reader cannot tell from the function signature that calling it changes persistent state. The bug is latent because RunPod serverless is single-process, but the pattern would not survive a multi-worker orchestrator or a model hot-reload.

**Recommendation.** Move the pad_token_id check into `startup()` (after `_load()`): `if self._processor.tokenizer.pad_token_id is None and self._processor.tokenizer.eos_token_id is not None: self._processor.tokenizer.pad_token_id = self._processor.tokenizer.eos_token_id`. This makes the mutation explicit and one-time. Add a comment that this is a one-shot init, not a per-call side effect.

**Verification.** Add a test in `workers/translategemma/tests/test_handler.py` that calls `_translate_batch` twice and asserts `self._processor.tokenizer.pad_token_id` is set only after `startup()`, not changed between calls. A simple way: assert the assignment is not in `_translate_batch`'s body via `inspect.getsource`.
