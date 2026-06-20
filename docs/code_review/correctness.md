---
branch: docs/code-review-initial
initial_review_commit: 23c29e1
last_updated_commit: 23c29e1
last_staleness_scan:
  commit: 23c29e1
  date: 2026-06-19
---

# Correctness

## CORR — General correctness

**Grade:** C

One critical defect: the default StreamingExecutor never checks `JobResult.status`, silently treating FAILED results as SUCCESS and propagating invalid outputs downstream. Four medium findings cover a dead batch executor duplicate, non-functional gRPC batch submission, SequentialExecutor losing error detail on exceptions, and ASR worker selection ignoring output-language capability. Two low findings flag dead defensive code and a latent non-linear-DAG limitation in the streaming executor.

### CORR-001 — StreamingExecutor ignores JobResult.status — FAILED results silently treated as SUCCESS

```yaml
status: verified
severity: critical
effort: M
reviewed_at: 23c29e1
last_verified_at:
  commit: pending
  date: 2026-06-19
fixed_in: ["pending"]
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
  commit: pending
  date: 2026-06-19
fixed_in: ["pending"]
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
  commit: pending
  date: 2026-06-19
fixed_in: ["pending"]
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
  commit: pending
  date: 2026-06-19
fixed_in: ["pending"]
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
  commit: pending
  date: 2026-06-19
fixed_in: ["pending"]
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
status: open
severity: low
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: pending
  date: 2026-06-19
fixed_in: []
files:
  - path: src/acheron/shell/executors/streaming.py
    lines: 97-100
related: []
```

**Issue.** `_consume_final_queue` (streaming.py:99) checks `isinstance(first, AcheronError)` on the first item from the final queue. The queue is typed `Queue[JobResult | None]` and stages only put `JobResult` objects or `None` (the `_END` sentinel) on queues — never `AcheronError`. Errors propagate via task exceptions caught by the TaskGroup, not via queue items. This check can never be True.

**Why it matters.** Dead defensive code that suggests a misunderstanding of the error flow. A reader might believe errors are passed through queues, obscuring the actual exception-based error mechanism. Low because it has no functional impact.

**Recommendation.** Remove the `isinstance(first, AcheronError)` check and the `raise first` branch. If the intent was to detect error conditions early, document that errors arrive via TaskGroup exceptions, not queue items.

**Verification.** Confirm no test relies on this check. Run the streaming executor test suite after removal to verify no regression.

### CORR-007 — Streaming executor _END propagation skips unrelated downstream steps in non-linear DAGs

```yaml
status: open
severity: low
effort: M
reviewed_at: 23c29e1
last_verified_at:
  commit: pending
  date: 2026-06-19
fixed_in: []
files:
  - path: src/acheron/shell/executors/streaming.py
    lines: 191-196
related: []
```

**Issue.** The streaming executor models the plan as a linear pipeline: stage N reads from stage N-1's queue. If stage N-1 sends `_END` (because it failed or was skipped), stage N reads `_END` and skips (streaming.py:195-196). For a non-linear DAG where step C depends on step A (not B), but the topological order is [A, B, C], C reads from B's queue. If B fails and sends _END, C is skipped — even though C's actual dependency (A) succeeded. The TODO at streaming.py:193-194 acknowledges this limitation for future branch support.

**Why it matters.** If a non-linear Plan DAG is passed to the StreamingExecutor, steps that should run (their actual dependencies succeeded) would be silently skipped, producing incomplete output. The current planner only generates linear chains, so this is latent — but the Executor interface accepts arbitrary Plans. Low because the current planner never produces non-linear DAGs.

**Recommendation.** Either reject non-linear DAGs in the StreamingExecutor (validate that each step depends on at most the immediately preceding step in topological order), or implement proper per-dependency queue fan-out. Until then, document the linear-only constraint in the class docstring.

**Verification.** Construct a Plan with a non-linear DAG (e.g., A->B, A->C, B+C->D) and run through StreamingExecutor. Verify either an explicit error is raised or all steps whose dependencies succeeded execute correctly.

## ML — ML correctness

**Grade:** A

No ML-specific findings. The codebase is not an ML training pipeline. Reviewed for data-flow integrity analogues (cross-job state contamination, step-cache key collisions, shared mutable state across concurrent jobs, look-ahead in pipeline ordering) — none found. Step cache keys are job_id/step_id (unique per job), GrpcWorker._batches is per-instance (no cross-job sharing), and no shared mutable state exists across concurrent jobs.

## MATH — Numerical correctness

**Grade:** A

No numerical correctness findings. Chunking uses character-count arithmetic with no off-by-one errors (verified `_hard_split`, `_merge_parts`, `_split_on_punctuation` boundary handling in core/chunking.py). Cost/duration aggregation is simple addition with no precision concerns. No divide-by-zero, NaN propagation, or float-comparison issues found.
