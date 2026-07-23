---
branch: code-review-refresh
initial_review_commit: 23c29e1
last_updated_commit: e0246e0019c0f3a6596c8ddef3dcf5af3405f5b8
last_staleness_scan:
  commit: e0246e0
  date: 2026-07-23
---

# Operations

## PERF — Performance

**Grade:** B

PERF-001, PERF-002, PERF-003 remain verified. PERF-004, PERF-005 remain open and kept (code unchanged since 63faed4). Two new PERF findings: PERF-006 (medium) — edge `/execute` buffers entire multipart body in memory with O(n²) append for `FileArtifact` streams; PERF-007 (medium) — per-call `httpx.AsyncClient` construction in health probes and pricing refresh (no connection reuse). PERF-008 (medium) remains open (per-call client in `_post_multipart`). **2026-06-26 round 2 refresh**: no new PERF findings; PERF-007 line numbers re-resolved (DOC-007 trimmed the pricing.py module docstring by 6 lines).

### PERF-001 — Health checks run sequentially, blocking the whole sweep on slow/dead workers

```yaml
status: verified
severity: medium
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in:
  - 0818bff23e451bdf61f183079d72e546c56e09a6
files:
  - path: src/acheron/shell/health.py
    lines: 89-112
related: [REPRO-002]
```

**Issue.** `HealthMonitor._check_all` (health.py:96-101) iterated registered workers and `await`ed `self._health_check(...)` plus the `record_health_*` calls one worker at a time. Each HTTP/gRPC probe has a 5s timeout. With W workers and K unreachable ones, a single sweep took up to K*5s + (W-K)*t before the next worker was even probed, and the 30s interval could slip or overlap.

**Why it matters.** A few dead workers serialized the entire health sweep, delaying removal of other unhealthy workers and delaying detection of newly-healthy ones; with many workers the monitor effectively stopped keeping up. Medium severity: degraded the reliability of the failure-detection loop under the exact condition it existed to handle.

**Recommendation.** Probe all workers concurrently with `asyncio.gather(*(self._health_check(w.endpoint, w.transport) for w in workers), return_exceptions=True)`, then process the results and fire the `record_health_*` calls.

**Verification.** Test with N fake workers where M sleep 5s before responding; assert `_check_all` completes in ~5s (one timeout window) rather than ~M*5s.

### PERF-002 — Registry list_all() called per step in dispatch hot path (N+1 round-trips)

```yaml
status: verified
severity: medium
effort: M
reviewed_at: 23c29e1
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in:
  - c8066e7
files:
  - path: src/acheron/shell/step_handler.py
    lines: 86-99
  - path: src/acheron/shell/orchestrator.py
    lines: 170
related: []
```

**Issue.** The step handler (step_handler.py:88) calls `await registry.list_all()` on EVERY step dispatch to find a matching worker, and orchestrator.submit_job calls list_all() again to compile the plan. For a plan with S steps this is S+1 list_all() calls; the registry is read-only during a single job's execution. For the Redis backend (stores/redis.py:308-317) each list_all() does `smembers` plus a pipelined `hgetall` per worker, so with W workers the dispatch hot path makes (S+1)*(1+W) Redis round-trips purely for worker discovery that could be satisfied by one snapshot.

**Why it matters.** Each redundant list_all() adds a Redis pipeline round-trip to every step; for a 5-step plan against 10 workers that's ~55 round-trips where 1 suffices, lengthening end-to-end latency and increasing Redis load under concurrent jobs. Medium severity: measurable latency on the main execution path, but not a correctness risk.

**Recommendation.** Snapshot the worker list once per plan execution and pass it into the handler (or cache list_all() on the store with invalidation on register/unregister). The executor could fetch workers once and thread the selected worker through, or the handler could memoize list_all() per plan_id.

**Verification.** Add a test asserting list_all() is called once per plan (not once per step) during execute(); instrument Redis and observe round-trip count drop from (S+1)*(1+W) to ~1+W.

### PERF-003 — Worker transport instances reconstructed per step (no HTTP connection or gRPC channel reuse)

```yaml
status: verified
severity: medium
effort: M
reviewed_at: 23c29e1
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in:
  - c8066e7
files:
  - path: src/acheron/shell/step_handler.py
    lines: 124-127
  - path: src/acheron/shell/transports/http.py
    lines: 28-44
  - path: src/acheron/shell/transports/grpc.py
    lines: 31-34
related: []
```

**Issue.** step_handler.py:112 builds a fresh `worker_instance = factory(selected)` on every step call. For HTTP this constructs a new HttpWorker that, lacking a shared client, opens a throwaway `httpx.AsyncClient` per request (http.py:48) — no keep-alive/connection reuse. For gRPC, `default_worker_factory` (step_handler.py:40) calls `grpc_channel(endpoint)` creating a new `grpc.aio.Channel` per step; channels are expensive to establish (TCP+TLS handshake). Across a multi-step plan and concurrent jobs this multiplies connection setup cost.

**Why it matters.** Per-step connection churn adds latency and file-descriptor pressure on the orchestrator and worker sides, and defeats HTTP keep-alive / gRPC channel multiplexing that exists precisely for repeated calls to the same endpoint. Medium severity: latency and resource overhead on the dispatch hot path, scaling with job concurrency.

**Recommendation.** Cache worker instances (or at least the underlying `httpx.AsyncClient` / `grpc.aio.Channel`) per worker_id/endpoint on the step handler or a small worker pool, reusing them across steps and jobs; close them on orchestrator shutdown.

**Verification.** Instrument channel/client construction; assert one Channel/AsyncClient per distinct worker endpoint across a multi-step plan rather than one per step.

### PERF-004 — HealthMonitor._check_all processes worker results sequentially with W Redis round-trips

```yaml
status: verified
severity: medium
effort: S
reviewed_at: 63faed4
last_verified_at:
  commit: pending
  date: 2026-07-23
fixed_in: ["pending"]
files:
  - path: src/acheron/shell/health.py
    lines: 113-145
related: [PERF-001]
```

**Issue.** After the concurrent probe `asyncio.gather` (health.py:118-121), `_check_all` iterates `for worker, result in zip(...)` and awaits each `record_health_success` (health.py:129) or `_handle_failure` (health.py:131) one at a time. For the Redis backend each call is 1-2 round-trips; with W workers that's W sequential awaits. PERF-001 made the probes concurrent but left the post-probe bookkeeping serial, so the overall sweep time on Redis is now dominated by W * (Redis RTT) + failure-handling latency.

**Why it matters.** Sweep latency is now W * ~2ms for a 10-worker fleet on a 1ms-RTT Redis. The health-monitor interval is 30s; if bookkeeping overruns the interval, the next sweep is delayed (the existing `await asyncio.sleep(self._interval)` happens after the loop returns), creating observable drift. More importantly, the inter-request serial pattern prevents the monitor from absorbing fleet growth: doubling the worker count doubles the sweep time even though the probe cost was constant.

**Recommendation.** Wrap the per-worker result handling in `asyncio.gather(*(self._handle_result(worker, result) for worker, result in zip(workers, results, strict=True)), return_exceptions=True)`. Hoist the `record_health_success` / `set_worker_status` / `record_health_failure` triad into a single helper so it can be `gather`-ed. The provider-check inside `_handle_failure` is already a single await, so no other change is needed for the gather to parallelize.

**Verification.** Instrument `_check_all` with a wall-clock timer. With 20 fake workers and a fake Redis that adds 2ms per call, assert the post-probe phase completes in <5ms (parallel) rather than >40ms (serial). Add a test that mocks 20 workers, intercepts `record_health_success`, and asserts the calls overlap (e.g. via call timestamps).

### PERF-005 — Provider status checks in _handle_failure run sequentially and can starve the health interval

```yaml
status: verified
severity: medium
effort: S
reviewed_at: 63faed4
last_verified_at:
  commit: pending
  date: 2026-07-23
fixed_in: ["pending"]
files:
  - path: src/acheron/shell/health.py
    lines: 122-145
related: [PERF-004]
```

**Issue.** `_handle_failure` (health.py:133-149) awaits `provider.check_status(endpoint_id)` synchronously for each failing worker. `RunPodHealthProvider.check_status` and `HuggingFaceHealthProvider.check_status` (health_providers.py:39-94) each have a 10s httpx timeout. The caller iterates failures one at a time inside `_check_all` (health.py:130-131). With N concurrent failures that all have a provider configured, the failure-handling phase can take N * 10s — already exceeding the 30s default interval at N=4, and there is no back-pressure to drop provider calls when the budget is consumed.

**Why it matters.** A platform-side outage (e.g. all RunPod workers go down at once) causes `_check_all` to block for tens of seconds, slipping the next interval and delaying detection of *new* health transitions for the *other* workers. This is the same class of regression PERF-001 fixed for HTTP probes: a slow dependent on a small number of unhealthy workers serializes the whole sweep. The new layer added by Layer 11 re-introduces the failure-mode in a different subsystem.

**Recommendation.** Either (a) `asyncio.gather` the `provider.check_status` calls across the failure batch, applying a per-batch budget (e.g. 5s overall) and defaulting to OFFLINE on timeout; or (b) use a shorter per-call timeout (3-5s) and an overall `asyncio.wait_for` ceiling; or (c) skip the provider check entirely once a worker is already known-OFFLINE in the local store to avoid redundant remote calls. Combine with PERF-004 to also parallelize the surrounding `record_health_success`/`record_health_failure` pipeline calls.

**Verification.** Register 5 workers all pointing at a fake provider endpoint that sleeps 10s before responding. Measure the time `_check_all` spends in failure handling: must be <5s (gathered) rather than >50s (serial). Add a regression test using a fake provider with controllable latency.

### PERF-006 — Edge `/execute` buffers entire multipart body in memory; O(n²) append for FileArtifact streams

```yaml
status: fixed
severity: medium
effort: S
reviewed_at: dbec2be
last_verified_at:
  commit: d7cabcb
  date: '2026-06-25'
fixed_in:
- d7cabcb
files:
- path: src/acheron/worker_sdk/_edge_http.py
  lines: 136-178
related:
- CORR-017
```

**Issue.** `_build_multipart_response` (lines 96-112) accumulates each artifact's body with `body_data = b""; ... body_data += chunk` and then concatenates every part with `b"".join(parts)`. For `FileArtifact` (artifacts.py:71-77) the stream yields 64 KiB chunks, so a 100 MiB chapter audio produces ~1600 chunks; each `bytes += bytes` allocates a fresh bytes object, giving O(n²) total allocation (~80 GiB of transient allocation for a 100 MiB artifact). The final `b"".join(parts)` then materialises the entire response in memory regardless of artifact size. For a batch inference of N chapters through the edge, the peak memory is the full response, and the orchestrator will read the same body in `HttpWorker._parse_multipart` (transports/http.py:96-98) — so the bytes are written to and read from RAM twice per `/execute`.

**Why it matters.** A 100 MiB chapter is plausible for a long-form TTS job (e.g., 90-minute audiobook chapter at 24 kHz mono PCM). The current shape will cause multi-hundred-MiB peak RSS on the edge container, which combined with the 1800 s execution timeout (settings.py:48) is a stability risk for back-to-back large jobs. Latent for the stub handlers (which emit ~10 KiB WAVs) but always present in the code path.

**Recommendation.** Replace the `body_data += chunk` pattern with `chunks: list[bytes] = []; async for chunk in a.stream(): chunks.append(chunk); body_data = b"".join(chunks)` — or stream the response body directly using a FastAPI `StreamingResponse` that yields header + body chunks in order. `StreamingResponse` also lets the orchestrator parser consume parts without holding the whole body in memory.

**Verification.** Add a unit test using a `StreamArtifact` whose producer yields 1000 x 1 MiB chunks; assert the peak RSS during `_build_multipart_response` stays under (total_size + 2 MiB), and that the resulting body is byte-identical to the eager form. Alternatively, mock `sys.allocated_blocks` before/after and assert allocations do not scale as O(n²).

### PERF-007 — Per-call `httpx.AsyncClient` construction in health probes and pricing refresh (no connection reuse)

```yaml
status: verified
severity: medium
effort: S
reviewed_at: dbec2be
last_verified_at:
  commit: pending
  date: 2026-07-23
fixed_in: ["pending"]
files:
- path: src/acheron/shell/health.py
  lines: 44-54, 82-123
- path: src/acheron/worker_sdk/pricing.py
  lines: 121-136, 204-210
- path: src/acheron/shell/transports/http.py
  lines: 92-123
- path: src/acheron/worker_sdk/app.py
  lines: 109-131
- path: src/acheron/shell/step_handler.py
  lines: 143-164
- path: src/acheron/shell/orchestrator.py
  lines: 207-221, 522-533
related: []
```

**Issue.** Three hot paths construct a fresh `httpx.AsyncClient` on every call: (a) `HealthMonitor` probes go through `_check_http_health` (health.py:46) which opens a new client per worker, every 30 s; (b) `RunPodPrice.refresh` (pricing.py:131) opens a new client per refresh; (c) `RunPodPrice.estimate` (pricing.py:201) opens a new client per estimate when the cache is stale. PERF-001 fixed the probe parallelism but the per-probe client construction is still in the path. For a 20-worker fleet with a 1ms-RTT upstream, the 20 probe clients (each doing TCP+TLS+HTTP/2 setup) dominate the sweep time after the per-probe parallelism is applied.

**Why it matters.** `httpx.AsyncClient` without explicit lifecycle re-uses the underlying httpcore connection pool across requests, but only WITHIN a single `AsyncClient` instance. Opening a new `AsyncClient` per call defeats keep-alive and forces fresh TCP+TLS handshakes on every request. For health probes that fire every 30s this is a constant low-grade tax; for `RunPodPrice` (called per `/execute`) it adds a full handshake to every job.

**Recommendation.** In health.py, store a single `httpx.AsyncClient` on the `HealthMonitor` instance (lazy-init in `start()`, close in `stop()`). In pricing.py, construct a single `AsyncClient` in `RunPodPrice.__init__` and reuse it; close it on a shutdown hook. Alternatively, accept a `client=` parameter in the constructors (DI seam) so tests can inject fakes and production can pin lifecycle.

**Verification.** Wrap the `AsyncClient` ctor in a counter; run a health sweep against 20 fake workers and a price refresh loop calling `estimate()` 20 times; assert the counter increments by 1 (one per process), not 20. Add a regression test that asserts the same client is reused across probes via a mock that records connection setup.

## OBS — Observability

**Grade:** A

OBS-001 (medium) and OBS-003 (low) remain open and kept (code unchanged since 63faed4). OBS-002 and OBS-004 remain verified. OBS-005 (medium) remains open. Three new OBS findings: OBS-006 (medium) — `RunPodClient` and `RunPodPrice` swallow transport / API errors with no log line; OBS-007 (medium) — edge `/execute` endpoint is unauthenticated; `docker-compose` exposes it on host network; OBS-008 (low) — `create_worker_app` lifespan catches `BaseException` around price refresh, masking `CancelledError` during shutdown. **2026-06-26 refresh**: OBS-007, OBS-009, OBS-010 marked verified (Bearer auth on `/execute` in `fa87bc6`); OBS-011, OBS-012 added — both low-effort observability gaps. **2026-06-26 round 2 refresh**: OBS-001 verified (commit `8f54443`); OBS-013 added — the new `_drain_inflight_tasks` is silent on entry/completion/timeout and an unhandled `TimeoutError` can break clean shutdown with no log breadcrumb.

### OBS-001 — Shutdown does not drain in-flight _execute tasks; cancelled jobs stay stuck at "running"

```yaml
status: verified
severity: medium
effort: M
reviewed_at: 23c29e1
last_verified_at:
  commit: 8f54443
  date: 2026-06-26
fixed_in: ["8f54443"]
files:
- path: src/acheron/shell/orchestrator.py
  lines: 252-280
- path: src/acheron/shell/orchestrator.py
  lines: 341-360
- path: tests/shell/test_orchestrator.py
  lines: 237-282
- path: tests/integration/test_job_lifecycle.py
  lines: 75-110
- path: tests/integration/test_multi_job.py
  lines: 47-70
related:
- OBS-004
```

**Issue.** `Orchestrator.shutdown()` stops only the health monitor; it never cancels or awaits the `_execute` tasks tracked in `self._tasks` (populated at `submit_job`). The FastAPI lifespan then calls `close()` which tears down the Redis pool. When the loop tears down, in-flight `_execute` tasks are cancelled mid-run; `CancelledError` is a `BaseException` so the `except AcheronError`/`except Exception` guards at the end of `_execute` don't catch it, and the final `await self._job_store.put(tracked)` sits outside any `finally`, so it is skipped. The job is left persisted with `status="running"` and never updated.

**Why it matters.** After any orchestrator restart, previously in-flight jobs are permanently stuck at "running" in the job store with nothing executing them; operators and the dashboard cannot distinguish truly-running from orphaned jobs. Medium severity: silent persisted-state corruption that misleads observability and can block cleanup/retry logic.

**Recommendation.** In `shutdown()`, cancel and await `_tasks` with a grace timeout, and move the `job_store.put(tracked)` in `_execute` into a `finally` block (setting status="failed" on `CancelledError`) so the persisted state always reflects reality. Alternatively, on `start()`, mark any persisted "running" jobs as "failed" (interrupted).

**Verification.** Start a job, call `shutdown()` mid-execution, then inspect the job store: the job should be "failed" (or otherwise reconciled), not "running". Add a test that cancels `_execute` and asserts `job_store.put` ran with a terminal status.

### OBS-002 — Dashboard silently swallows orchestrator connection errors

```yaml
status: verified
severity: low
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in:
  - be7b3ab
files:
  - path: dashboard/app.py
    lines: 27-37
related: []
```

**Issue.** `_fetch` (dashboard/app.py:27-34) catches `httpx.HTTPError, OSError` and returns `{}` with no logging. When the orchestrator is unreachable, the jobs/workers/cost partials render empty data ("No jobs found.") with no indication to the operator that the dashboard backend is down rather than the system being empty.

**Why it matters.** An operator cannot distinguish "no jobs" from "dashboard can't reach orchestrator" by looking at either the UI or logs, delaying diagnosis of a connectivity outage. Low severity: monitoring gap, not a functional failure.

**Recommendation.** Log the exception in `_fetch` (`logger.warning`) and/or surface an error banner partial when the fetch returns empty due to an exception.

**Verification.** Point the dashboard at a stopped orchestrator and confirm a log warning is emitted and/or an error indicator renders instead of "No jobs found."

### OBS-003 — Logs are free-form with no structured fields or trace correlation

```yaml
status: fixed
severity: low
effort: L
reviewed_at: 23c29e1
last_verified_at:
  commit: 5af8162
  date: 2026-06-25
fixed_in:
- 5af8162
files:
- path: src/acheron/shell/orchestrator.py
  lines: 263-270
- path: src/acheron/shell/health.py
  lines: 113-152
- path: src/acheron/shell/step_handler.py
  lines: '137'
- path: dashboard/app.py
  lines: 27
related: []
```

**Issue.** All logging uses free-form `%s` format strings (e.g. `orchestrator.py:230-237`). There is no structured/JSON logging and no correlation token beyond job_id appearing inside message text. The Layer 11 diff adds more free-form `logger.warning`/`logger.info` calls in `health.py` and `orchestrator.py`, not structured. The new worker_sdk adds only free-form `%s` logging (registration.py:71, 74; _edge_http.py:164; app.py:36, 48, 123) — consistent with the existing convention, not an improvement. In a distributed system with concurrent jobs, workers, and health checks, correlating a failure across orchestrator→transport→worker requires grepping free-text rather than filtering on fields.

**Why it matters.** Free-form logs are harder to query and aggregate in prod log systems and lack stable field names for job_id/worker_id/step_id/trace_id, weakening cross-component traceability. Low severity: a consistency/observability gap, not a functional failure.

**Recommendation.** Adopt structured logging (e.g. structlog or stdlib `extra=` with a JSON formatter) with stable fields (job_id, worker_id, step_id, strategy) so failures trace across orchestrator→transport→worker by field rather than text.

**Verification.** Run a job end-to-end and confirm log entries carry job_id/step_id as queryable fields in the emitted JSON.

### OBS-004 — Top-level execution failures set status but persist no error detail

```yaml
status: verified
severity: low
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in:
  - 92ed9da
files:
  - path: src/acheron/shell/orchestrator.py
    lines: 213-238
related: [CORR-004, OBS-001]
```

**Issue.** When `executor.run()` raises `AcheronError` or `Exception`, `_execute` (orchestrator.py:213-238) sets `tracked.status = PlanStatus.FAILED` and now populates `tracked.result` with a minimal `PlanResult` carrying the error string. The API's `_tracked_to_response` (jobs.py:72-83) returns `errors=[]` when result is None, so a consumer of `GET /jobs/{id}` sees `status="failed"` with no error detail — the failure reason exists only in server logs. The PlanStatus enum fix changed the status assignment but did not address the missing error detail.

**Why it matters.** Operators diagnosing failures via the API see a failed job with an empty error list, forcing them to correlate logs by job_id/time. Low severity: the status is correct and the detail is in logs, but the API is misleading for triage.

**Recommendation.** When `_execute` catches a top-level exception, synthesize a minimal PlanResult (or extend TrackedJob with an error field) so the failure reason is persisted alongside the status.

**Verification.** Trigger a worker failure that propagates out of `executor.run()`; `GET /jobs/{id}` and assert `errors` is non-empty and names the failure.

### OBS-005 — Health providers swallow `(httpx.HTTPError, OSError)` silently with no diagnostic log

```yaml
status: verified
severity: medium
effort: S
reviewed_at: 63faed4
last_verified_at:
  commit: 1fbedbc
  date: '2026-06-24'
fixed_in:
- 1fbedbc
files:
- path: src/acheron/shell/health_providers.py
  lines: 49-50
- path: src/acheron/shell/health_providers.py
  lines: 80-81
related:
- CORR-010
- EXC-003
```

**Issue.** Both `RunPodHealthProvider.check_status` (health_providers.py:49-50) and `HuggingFaceHealthProvider.check_status` (health_providers.py:80-81) catch `(httpx.HTTPError, OSError)` and silently return `WorkerStatus.OFFLINE`. The blanket `except` erases the distinction between (a) provider API key is invalid/wrong (401/403), (b) provider is rate-limiting (429), (c) network is down, (d) endpoint_id does not exist (404), and (e) provider service is degraded (5xx). The caller in `health._handle_failure` does log a warning when the provider itself raises (health.py:142), but the providers' `except` block short-circuits before that path is reached, so the user has no log evidence of the actual failure mode.

**Why it matters.** When all HF workers are reported BOOTING (false positive) or OFFLINE (false negative) the operator cannot diagnose whether the orchestrator is misconfigured (wrong API key) or the platform is the problem. The fallback to OFFLINE on auth failure is especially bad: a typo'd `${HF_API_KEY}` will silently mark every HF-endpoint worker as OFFLINE on every health cycle, with no log line pointing at the cause. This compounds the `${VAR}` silent-fail in `config._expand_env_vars` (config.py:18-26) — if the env var is unset, the provider is not even instantiated, also with no warning.

**Recommendation.** In each provider's `except (httpx.HTTPError, OSError) as exc:` block, emit a structured warning: `logger.warning("%s health check for %s failed: %s", self.__class__.__name__, endpoint_id, exc)` before returning OFFLINE. Differentiate 401/403 from 5xx via the response status code (the `resp` object is in scope before the `return` in the 4xx/5xx branch on health_providers.py:53 / 82-83). Also log a warning at `create_health_providers` (health_providers.py:108-114) when an expected `api_key` is empty after env-var expansion.

**Verification.** Configure `providers.huggingface.api_key: "${HF_API_KEY}"` and leave the env var unset: orchestrator startup should log a warning naming the missing provider. Set a deliberately wrong `HF_API_KEY` and force a worker to enter the failure path: assert the log line includes the HTTP 401/403 status and the provider name.

### OBS-006 — `RunPodClient` and `RunPodPrice` swallow transport / API errors with no log line

```yaml
status: verified
severity: medium
effort: S
reviewed_at: dbec2be
last_verified_at:
  commit: pending
  date: 2026-07-23
fixed_in: ["pending"]
files:
  - path: src/acheron/worker_sdk/_runpod_client.py
    lines: 74-127
  - path: src/acheron/worker_sdk/pricing.py
    lines: 130-156
related: [CORR-014]
```

**Issue.** `RunPodClient.run` (pricing/_runpod_client.py:75-93) wraps endpoint construction in `asyncio.to_thread(_open_endpoint, ...)` with no try/except — failures (typo'd endpoint id, missing permissions, expired API key) bubble out as raw runpod SDK exceptions with no log line. `RunPodPrice._refresh_rate` (pricing.py:131-147) catches the union `(httpx.HTTPError, OSError, KeyError, ValueError, TypeError)` and returns False with no log; the same anti-pattern flagged in OBS-005 is now repeated in the new pricing module. Combined with the security impact in SEC-013 (API key as URL param), an operator seeing static $0 estimates has no log evidence to diagnose whether the rate endpoint rejected the key, rate-limited, or the GPU type simply isn't in the cache.

**Why it matters.** Pricing is best-effort (`_refresh_rate` returns False → cost_basis=CACHED), but a permanently broken rate lookup silently degrades billing accuracy. The pricing module deliberately trades correctness for availability, which is fine — but the absence of a single log line on the failure means the operator never knows the cache is stale forever. Violates the same convention OBS-005 flagged in health_providers.

**Recommendation.** In `RunPodPrice._refresh_rate`, log a structured warning naming the `endpoint_id`, the exception class, and the HTTP status (if applicable) before returning False. In `RunPodClient.run`, wrap the `to_thread` calls with a try/except that logs the exception context (`endpoint_id`, timeout, payload size) and re-raises as a `WorkerError` with a sanitized message — keeping the raw exception in `logger.exception(...)` only.

**Verification.** Point `RunPodPrice` at a fake GraphQL endpoint returning 401 once, then 200; assert exactly one warning log line per failed refresh, naming `endpoint_id` and the HTTP status. For `RunPodClient`, mock `endpoint.run()` to raise a runpod exception; assert the exception is logged with the `endpoint_id` and is re-raised as `WorkerError`.

### OBS-007 — Edge `/execute` endpoint is unauthenticated; `docker-compose` exposes it on host network (8004:8001)

```yaml
status: verified
severity: medium
effort: S
reviewed_at: dbec2be
last_verified_at:
  commit: fa87bc6
  date: 2026-06-24
fixed_in: [fa87bc6]
files:
  - path: src/acheron/worker_sdk/_edge_http.py
    lines: 156-163
  - path: src/acheron/worker_sdk/_edge_http.py
    lines: 167-186
  - path: src/acheron/worker_sdk/_edge_http.py
    lines: 233-271
  - path: docker-compose.yml
    lines: 166-198, 200-231, 233-265
  - path: docker-compose.yml
    lines: 166-198, 200-231, 233-265
  - path: docker-compose.yml
    lines: 166-198, 200-231, 233-265
related: [SEC-005, SEC-014, OBS-009, OBS-010]
```

**Issue.** The edge container's POST `/execute` handler (`_edge_http.py:151-194`) has no auth dependency — the only auth in the SDK flow is the registration token on POST `/workers`. `docker-compose.yml:170-171` maps 8004:8001, so any process on the host (or anyone able to reach the host on port 8004) can call `/execute` directly, bypassing the orchestrator's job-submission path. `/execute` accepts an arbitrary `ExecuteRequest` with any job_id and any payload, so a host-side attacker can: (a) consume the edge's RunPod credits by submitting fabricated jobs, (b) probe for the RunPod endpoint to learn the endpoint_id via the timing/exception response, (c) use the edge as a free proxy to the RunPod serverless endpoint with their own payloads. This compounds SEC-005 (orchestrator job routes are also unauthenticated).

**Why it matters.** The assumption is that the edge is on a trusted internal network, but the compose file maps the port to the host, breaking that assumption. The `/execute` endpoint is the entire cost-bearing surface of the RunPod edge — a single host-level access yields a billable surface. The single-job pricing already exposes RunPod endpoint_id in the error response (see SEC-012), so an attacker who can reach `/execute` can correlate errors with endpoint_id.

**Recommendation.** Either (a) require an `Authorization: Bearer <registration_token>` dependency on `/execute` that mirrors the orchestrator's `verify_registration_token`, and gate the port to `expose:` instead of `ports:` in compose so it is not host-reachable; or (b) drop the `ports:` mapping in `docker-compose.yml` and document that the edge must be on an internal-only Docker network. Apply the same check to all stub services that use `create_worker_app`.

**Verification.** From the host, `curl -X POST http://localhost:8004/execute -d '{...}'` should be rejected (401/403). For (a), assert the `Authorization` header is required and that a missing header produces 401. Update compose to `expose: [8001]` and re-run the curl from the host — it should fail with a connection refused.

### OBS-008 — `create_worker_app` lifespan catches `BaseException` around price refresh, masking `CancelledError` during shutdown

```yaml
status: verified
severity: low
effort: S
reviewed_at: dbec2be
last_verified_at:
  commit: 1fbedbc
  date: '2026-06-24'
fixed_in:
- 1fbedbc
files:
- path: src/acheron/worker_sdk/app.py
  lines: 123-129
related:
- EXC-004
```

**Issue.** The outer lifespan in `create_worker_app` (app.py:115-133) wraps `await price_source.refresh()` in `except BaseException: ... logger.warning(...)`. The `# noqa: BLE001` annotation marks the broad catch, but a `BaseException` handler swallows `CancelledError`, `KeyboardInterrupt`, `SystemExit`, and `asyncio.InvalidStateError` in addition to legitimate exceptions. During container shutdown the orchestrator's signal handler cancels the lifespan task; the cancellation passes through the price refresh, hits the `BaseException` handler, and the worker logs a misleading `Price refresh raised at startup; worker will register anyway` warning. Registration then runs (the registration retry loop is not in this try block, so it is not actually affected), but the operator sees a spurious 'price refresh raised' warning on every clean shutdown. The same `BaseException`-catch pattern in `_edge_http._run_execute` (line 162) is intentional (to return a JSON failure to the orchestrator) but is also brittle — a `CancelledError` that arrives between the start time and the `except BaseException` will be converted to a 500 response instead of propagating, masking shutdown.

**Why it matters.** Violates the OBS convention flagged in the existing review: broad-catch handlers produce misleading log lines. The container lifecycle is small enough that this isn't critical, but the pattern is reusable: a future /execute handler that copies this shape will inherit the same shutdown-misdiagnosing behavior.

**Recommendation.** In `app.py:122`, narrow the except to `(Exception,)` (or specific exception types RunPodPrice declares). Remove the BLE001 noqa. For `_edge_http.py:162`, keep the broad catch but explicitly re-raise `asyncio.CancelledError` and `KeyboardInterrupt` to let shutdown propagate: `except (asyncio.CancelledError, KeyboardInterrupt): raise` followed by `except BaseException as exc:` for everything else.

**Verification.** Send SIGTERM to the edge container while a price refresh is in flight; assert no `Price refresh raised at startup` warning is logged on a clean shutdown. Add a test that cancels the lifespan task mid-refresh and asserts the CancelledError propagates and the registration retry does not run.

## SEC — Security

**Grade:** A

SEC-001 through SEC-004 remain verified. SEC-005, SEC-006, SEC-007, SEC-008, SEC-009, SEC-010 remain open and re-confirmed (no code change since 63faed4). Five new SEC findings: SEC-011 (high) — `ACHERON_REGISTRATION_TOKEN` defaults to publicly-known `dev-registration-token` in compose and `.env.example`; SEC-012 (low) — edge `/execute` returns raw `str(exc)` in 500 body, exposing internal exception detail to the orchestrator; SEC-013 (medium) — `RunPodPrice` sends API key as URL query parameter instead of Authorization header; SEC-014 (medium) — `worker.edge.yaml` default `orchestrator_url` is HTTP, so registration token is sent in cleartext when env var is not overridden; SEC-015 (low) — all Docker images run as root with no `USER` directive. **2026-06-26 refresh**: SEC-011, SEC-018, SEC-022 marked verified (9b4adb6 enforces non-public 32+ char token); SEC-016 added — `worker.edge.yaml` HTTPS default for the new translategemma edge.

### SEC-001 — Dev cert private keys written world-readable (mode 0644)

```yaml
status: verified
severity: medium
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in:
  - 37245dd35f66457458e35dbb724267d3015b797c
files:
  - path: scripts/generate_dev_certs.py
    lines: 38-49
related: []
```

**Issue.** `_write_pem_key` (generate_dev_certs.py:38-49) chmodded every private key — including the CA key (line 96) and all service keys — to `0o644` (world-readable). The CA private key at 0644 let any local user sign certificates trusted by the dev CA.

**Why it matters.** World-readable private keys violate the general security rule that key material must be 0600; the CA key exposure in particular enables rogue cert signing. Even in dev this establishes a bad pattern that can leak if certs are copied into a shared image or host. Medium severity: dev-only blast radius, but CA key signing capability is a real local privilege risk.

**Recommendation.** Write private keys with mode `0o600` (owner read/write only); keep certificates at `0o644`.

**Verification.** Run `just certs` and `stat -c '%a' certs/*.key`; assert keys are 0600 and certs are 0644.

### SEC-002 — Worker registration fails open when ACHERON_REGISTRATION_TOKEN is unset

```yaml
status: verified
severity: medium
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in:
  - 5821b13759c2299583a7e056aba9f96232d2e92b
files:
  - path: src/acheron/shell/api/deps.py
    lines: 22-31
related: []
```

**Issue.** `verify_registration_token` (deps.py:24-26) returned immediately with no error when `ACHERON_REGISTRATION_TOKEN` was unset, allowing open registration. A prod deployment forgetting the env var silently accepted worker registrations from any client — including a malicious worker whose endpoint points at an attacker server receiving job payloads (source EPUBs/audio).

**Why it matters.** Fail-open auth on worker registration can leak job data to attacker-controlled endpoints with no log signal. Medium severity: requires a misconfiguration but produces silent data exposure in prod.

**Recommendation.** Log a WARNING at startup when the token is unset, and require an explicit `ACHERON_OPEN_REGISTRATION=1` opt-in for open registration.

**Verification.** Start the API with no token set and assert registration is rejected (503) unless `ACHERON_OPEN_REGISTRATION=1` is set; with the flag, registration succeeds.

### SEC-003 — TLS silently disabled when CA env vars are unset (gRPC insecure_channel / uvicorn plain HTTP)

```yaml
status: verified
severity: medium
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in:
  - 0b78d49416a703dbc6f3d5de723d3fb196b89380
files:
  - path: src/acheron/shell/tls.py
    lines: 52-75
  - path: src/acheron/shell/api/__main__.py
    lines: 19-26
related: [CFG-002]
```

**Issue.** `grpc_channel_credentials` (tls.py:74) returned `None` when neither `ACHERON_TLS_CA_FILE` nor `SSL_CERT_FILE` was set, and `grpc_channel` then returned `grpc.aio.insecure_channel` — so all orchestrator→worker gRPC traffic went plaintext with no warning. Likewise `uvicorn_ssl_kwargs` returned `{}` when cert/key were unset, serving plain HTTP. The CLI defaulted to HTTPS but the server defaulted to no TLS, an asymmetric and easy-to-misconfigure default.

**Why it matters.** A prod deployment missing the CA env var got plaintext transport of potentially sensitive job content with no log signal that encryption was off. Medium severity: silent transport-security downgrade on misconfiguration.

**Recommendation.** Log a WARNING at startup when TLS is not configured on the server or when the gRPC channel falls back to insecure; consider requiring an explicit `ACHERON_ALLOW_INSECURE=1` for non-TLS prod operation.

**Verification.** Start the API and a gRPC worker with no TLS env vars; assert a visible startup warning naming the plaintext fallback, and assert `grpc_channel` only returns `insecure_channel` under explicit opt-in.

### SEC-004 — Dashboard trusts spoofable X-Forwarded-User header as identity

```yaml
status: verified
severity: low
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in:
  - be7b3ab
files:
  - path: dashboard/app.py
    lines: 39-47
related: []
```

**Issue.** The index route (dashboard/app.py:38) reads `X-Forwarded-User` directly from the request and renders it as "Signed in as {{ user }}" (index.html:27). Any client can set this header to any value; nothing validates it against an authenticated session. Jinja2 autoescape is on (no XSS), so the impact is a misleading identity display rather than script injection.

**Why it matters.** The header is trivially forgeable unless an upstream proxy strips and re-injects it; absent that guarantee the "signed in as" indicator is meaningless and can mislead operators. Low severity: cosmetic/display only, no access decision depends on it.

**Recommendation.** Only honor `X-Forwarded-User` when an explicit trusted-proxy configuration is in place, or drop the identity display until real auth is wired; document the proxy assumption if the header is kept.

**Verification.** Send a request with `X-Forwarded-User: admin` directly to the dashboard; confirm the displayed identity is not trusted without a configured proxy.

### SEC-005 — Job submission/listing/capabilities routes require no authentication

```yaml
status: fixed
severity: low
effort: M
reviewed_at: 23c29e1
last_verified_at:
  commit: 050c578
  date: 2026-06-26
fixed_in: ["050c578"]
files:
  - path: src/acheron/shell/api/routes/jobs.py
    lines: 20-69
  - path: src/acheron/shell/api/routes/capabilities.py
    lines: 13-23
related: [OBS-007]
```

**Issue.** Only the worker-registration route injects `RegistrationTokenDep` (workers.py:22). Job submission (jobs.py:21), job listing (jobs.py:66), get_job, and capabilities (capabilities.py:14) have no auth dependency at all, so any network-reachable client can submit jobs (consuming worker resources/cost) and enumerate worker endpoints. The `X-Forwarded-User` pattern in the dashboard suggests an upstream proxy is assumed to handle auth, but that assumption is undocumented.

**Why it matters.** If the orchestrator is reachable outside a trusted network, unauthenticated job submission is a cost/resource abuse vector and worker-endpoint enumeration aids targeting. Low severity: likely intentional for an internal service behind a proxy, but the assumption is unstated and easy to violate.

**Recommendation.** Document the trusted-network/proxy assumption explicitly, or add an optional auth dependency (e.g. the same token or an API key) to the mutating routes gated by an env var so prod can enforce it without changing the dev default.

**Verification.** Confirm the documented deployment model states the auth boundary; if an auth dependency is added, assert unauthenticated `POST /jobs` is rejected when the token is set.

### SEC-006 — Raw exception strings exposed in PlanResult.errors via OBS-004 fix

```yaml
status: fixed
severity: low
effort: S
reviewed_at: be7b3ab
last_verified_at:
  commit: dfe5d92
  date: 2026-06-25
fixed_in:
- dfe5d92
files:
- path: src/acheron/shell/orchestrator.py
  lines: 362-384
related:
- OBS-004
- SEC-010
- SEC-012
```

**Issue.** The OBS-004 fix persists raw `str(exc)` from the top-level `except Exception` handler directly into `tracked.result.errors`. Unexpected exceptions may contain worker endpoints, file paths, library internals, or other implementation details that are now returned by `GET /jobs/{id}`.

**Why it matters.** Before the fix these details only lived in server logs; now they are exposed in API responses to anyone who can query job status, broadening the information-disclosure surface for the unauthenticated job routes noted in SEC-005.

**Recommendation.** Keep `logger.exception` for the full traceback, but populate `PlanResult.errors` with a sanitized or categorized message. For generic `Exception`, return a generic failure message and put the original exception detail in logs only.

**Verification.** Submit a job that fails with an exception whose message contains an internal path or endpoint; assert `GET /jobs/{id}` returns an error that does not contain that internal detail.

### SEC-007 — Host Path Traversal & Arbitrary Local File Read in ExtractionHandler

```yaml
status: verified
severity: high
effort: M
reviewed_at: d9dc740
last_verified_at:
  commit: 0450dc31a8244560d5dd8da18ecb1e4ae8013cc5
  date: 2026-06-24
fixed_in: [0450dc31a8244560d5dd8da18ecb1e4ae8013cc5]
files:
  - path: src/acheron/shell/local_handlers.py
    lines: 242-294
  - path: src/acheron/core/errors.py
    lines: 8-10
related: []
```

**Issue.** `ExtractionHandler` (local_handlers.py:242-265) accepts the `source_path` parameter directly from the user-controlled `job.payload` and reads or copies it without verifying that it lies within a permitted/sandboxed directory. For non-EPUB formats, it calls `_copy_audio` which copies the file directly into the step cache folder.

**Why it matters.** This allows any client to read arbitrary files accessible to the orchestrator process (e.g., `/etc/passwd`, private keys, env configurations) by submitting a request with `source_path` pointing to a sensitive file, posing a critical security risk.

**Recommendation.** Validate that `source_path` resolves to a path within a designated safe directory (e.g. using `Path.resolve()`), or implement strict sandboxing.

**Verification.** Submit an audio job with a path pointing to a sensitive file outside the workspace and verify it is rejected.

### SEC-008 — Auto-generated registration token is logged in plaintext at startup

```yaml
status: verified
severity: critical
effort: S
reviewed_at: 63faed4
last_verified_at:
  commit: b1d1d05
  date: 2026-06-24
fixed_in: [b1d1d05]
files:
  - path: src/acheron/shell/orchestrator.py
    lines: 196
related: [SEC-002, MAINT-006, SEC-009, SEC-011]
```

**Issue.** `Orchestrator.start()` generates a 32-char token via `secrets.token_hex(16)` (orchestrator.py:188) and immediately emits it to the logger at INFO level: `logger.info("Generated and persisted registration token: %s", token)` (orchestrator.py:192). Any operator, log shipper, or downstream aggregation system (Datadog, Loki, journald, etc.) that ingests orchestrator logs now holds a valid bearer token for the worker-registration route. Because the verification path in `verify_registration_token` (deps.py:48) accepts any client that presents this token, an attacker with log access can register an arbitrary `endpoint` and receive job payloads (EPUB chapters, audio, intermediate artifacts) — the same exposure profile that SEC-002 mitigates is now undermined by the auto-generation feature designed to replace the default token.

**Why it matters.** The token is the sole authentication on `POST /workers` and gates who can advertise themselves as a worker. Logging it converts a well-scoped secret into a globally-readable one. A single log dump, a misconfigured log retention policy, or a leaked log-storage credential is sufficient to register a malicious worker and exfiltrate job data. The fix to SEC-002 (fail-closed on missing token) is partially undone because prod deployments that relied on the auto-generated token now leak it on every restart.

**Recommendation.** Remove the token value from the log message: `logger.info("Generated and persisted registration token to %s", token_file)`. If a human needs to copy the token for worker setup, log a one-time hint such as `logger.info("Generated registration token; see %s", token_file)` and rely on the persisted file (which is also the documented retrieval path). Also confirm the token file mode is 0600 (see SEC-009).

**Verification.** Start a fresh orchestrator with `ACHERON_DATA_DIR=$(mktemp -d)`, grep the resulting log for the generated token — it must not appear. Add a test that boots an orchestrator in a tmp dir and asserts the INFO log line does not contain the token. Additionally assert `stat -c '%a' $DATA_DIR/.registration_token` returns 600.

### SEC-009 — Registration token file created with process umask (potentially world-readable)

```yaml
status: verified
severity: high
effort: S
reviewed_at: 63faed4
last_verified_at:
  commit: 7472ebc
  date: 2026-06-24
fixed_in: [7472ebc]
files:
  - path: src/acheron/shell/orchestrator.py
    lines: 182-199
related: [SEC-001, SEC-008, SEC-011]
```

**Issue.** `Orchestrator.start()` writes the generated (or re-loaded) registration token via `token_file.write_text(token, encoding="utf-8")` (orchestrator.py:191) without setting a restrictive mode. The token file is then re-loaded from disk on every subsequent start (orchestrator.py:179-185) and grants bearer access to the worker-registration route. On a default umask of 022 the file is created world-readable (0o644); even on a typical 002 umask the file is group-readable. This is the same anti-pattern that SEC-001 flagged for CA private keys and is explicitly contradicted by the `OSError` write test fixture used in `tests/shell/test_orchestrator.py:419-439`, which never asserts file mode.

**Why it matters.** Token reads are a one-time, local-credential path: any local user (or any process running as a different user) on the orchestrator host can register workers. In container deployments with shared PID namespaces, or in CI/dev environments where the data dir is bind-mounted, the blast radius extends beyond a single user. This complements SEC-008: even if the log exposure is fixed, the on-disk file is still a high-value secret without explicit protection.

**Recommendation.** After `token_file.write_text(...)`, call `os.chmod(token_file, 0o600)` (or open the file with `opener` set to enforce mode 0o600 atomically). Apply the same fix to the persist path on line 191 and to any future re-write path. Update the orchestrator tests to assert `token_file.stat().st_mode & 0o777 == 0o600`.

**Verification.** Run the orchestrator with a fresh data dir and assert `stat -c '%a' $DATA_DIR/.registration_token` returns 600. Add a regression test in `tests/shell/test_orchestrator.py::test_orchestrator_generates_and_persists_registration_token` that introspects the file mode and fails on 0o644 / 0o640.

### SEC-010 — Worker `last_error` exposed via unauthenticated /workers endpoint (info disclosure)

```yaml
status: fixed
severity: low
effort: S
reviewed_at: 63faed4
last_verified_at:
  commit: 069a535
  date: 2026-06-25
fixed_in:
- 069a535
files:
- path: src/acheron/shell/api/routes/workers.py
  lines: 65-71
- path: src/acheron/shell/health.py
  lines: 44-64
related: [SEC-005, SEC-006, OBS-005]
```

**Issue.** `/workers` (workers.py:56) has no auth dependency and now serializes the new `last_error` field (workers.py:69) populated from `f"{type(exc).__name__}: {exc}"` (health.py:52, 64) and `f"{error}; provider {provider_name} error: {exc}"` (health.py:144). The exception payload commonly embeds the target URL and underlying socket details — e.g. `httpx.ConnectError: All connection attempts failed: 10.0.5.7:8443`, gRPC `AioRpcError` with target host, or DNS resolution errors. These are returned verbatim to anyone who can reach the API. The companion `/partials/workers` dashboard route re-emits the same field via the `<pre>{{ w.last_error }}</pre>` element in `dashboard/templates/partials/workers.html`.

**Why it matters.** Builds on the SEC-005 unauthenticated-routes posture: an attacker with network reach can now enumerate the orchestrator's internal worker topology (internal IPs, ports, DNS names, transport details) by repeatedly failing the workers and reading the structured error blobs. The dashboard surface is autoescaped (Jinja2 default), so this is pure info disclosure rather than XSS. Combined with SEC-006's `str(exc)` in `PlanResult.errors`, the system now has two unauthenticated endpoints that surface internal exception detail.

**Recommendation.** Either (a) categorize `last_error` into a small set of public-safe buckets (`http_5xx`, `connection_refused`, `timeout`, `auth_failed`, `provider_offline`) before persisting, keeping the raw `str(exc)` only in `logger.exception(...)`; or (b) gate the `/workers` list and `/partials/workers` endpoints behind the same `RegistrationTokenDep` (or a new optional auth) so the field is only available to operators. Document the public-safe shape in `WorkerResponse` if you take (a).

**Verification.** Register a worker with `endpoint=http://10.0.5.7:8443`, force a failure, then `curl /workers | jq '.workers[].last_error'` and confirm no IP, port, or DNS detail appears. If a sanitized form is chosen, assert the value matches one of the documented buckets.

### SEC-011 — `ACHERON_REGISTRATION_TOKEN` defaults to publicly-known `dev-registration-token` in compose and `.env.example`

```yaml
status: verified
severity: high
effort: S
reviewed_at: dbec2be
last_verified_at:
  commit: 9b4adb6
  date: 2026-06-24
fixed_in: [9b4adb6]
files:
  - path: docker-compose.yml
    lines: 35
  - path: docker-compose.yml
    lines: 95
  - path: docker-compose.yml
    lines: 175
  - path: docker-compose.yml
    lines: 209
  - path: docker-compose.yml
    lines: 242
  - path: .env.example
    lines: 7
related: [SEC-008, SEC-009, SEC-022, DOC-003]
```

**Issue.** `docker-compose.yml` hardcodes `${ACHERON_REGISTRATION_TOKEN:-dev-registration-token}` for orchestrator (line 35) and the worker-side `ACHERON_WORKER__REGISTRATION_TOKEN` (line 175) — if the operator forgets to set `ACHERON_REGISTRATION_TOKEN` in their `.env` (a common mistake on first `docker compose up`), the system falls back to the literal string `dev-registration-token`, which is also in `.env.example` (line 7) and therefore a publicly known value. Any attacker who has read the `.env.example` or the compose file (both committed to the repo) can present `Authorization: Bearer dev-registration-token` to POST `/workers` and register a malicious worker endpoint that will receive every job payload. SEC-002 fixed the fail-open case where the env var is unset in the orchestrator, but the dev default slips a known token through the otherwise-closed check.

**Why it matters.** The security model relies on the registration token being a secret. A documented default in a public file is functionally equivalent to no auth for the deployment footgun it causes. The default does not just bypass auth on misconfiguration; it forces every naive `cp .env.example .env` deployment to ship with a public-secret worker registration. This is the kind of pattern CVEs are filed against.

**Recommendation.** Remove the `:-dev-registration-token` fallback in `docker-compose.yml` so the env var is required; make `.env.example` document the variable with a placeholder like `ACHERON_REGISTRATION_TOKEN=` (empty) and a comment instructing the operator to generate one with `openssl rand -hex 32`. Update the orchestrator's startup to fail closed if the env var is the empty string (currently it only fails closed when unset). Add a CI / startup check that refuses to boot if the token equals `dev-registration-token` or is shorter than 32 chars.

**Verification.** Run `docker compose up` with no `.env` file; assert the orchestrator logs a `ACHERON_REGISTRATION_TOKEN must be set` error and exits non-zero. Run with `ACHERON_REGISTRATION_TOKEN=dev-registration-token`; assert the same refusal. Run with a freshly generated 32-char token; assert registration succeeds.

### SEC-012 — Edge `/execute` returns raw `str(exc)` in 500 body, exposing internal exception detail to the orchestrator (extension of SEC-006)

```yaml
status: fixed
severity: low
effort: S
reviewed_at: dbec2be
last_verified_at:
  commit: bc5fce1
  date: 2026-06-25
fixed_in:
- bc5fce1
files:
- path: src/acheron/worker_sdk/_edge_http.py
  lines: 286-304
related:
- SEC-006
- OBS-007
```

**Issue.** `_run_execute` (lines 162-178) catches `BaseException` from `self.handler.handle(job)` and returns a JSON body of `JobResult(job_id=..., status=FAILED, error=str(exc), ...)`. The orchestrator's `HttpWorker._request` (transports/http.py:70-73) catches `httpx.HTTPStatusError`, embeds `exc.response.text` into a `WorkerError`, and the orchestrator's `_execute` (orchestrator.py:332-344) puts that into `PlanResult.errors`. The end-to-end effect is identical to SEC-006: internal exception detail flows through `PlanResult.errors` to any caller of `GET /jobs/{id}`. For the qwen3tts handler, `str(exc)` commonly includes PyTorch device strings (e.g. `RuntimeError: CUDA error: device-side assert triggered`), torch tensor shapes, the underlying RunPod endpoint id, or even the model path on the RunPod serverless image. The error is also stored on the worker side via the `logger.exception` call (line 164) which is good, but the response body and downstream API expose the same string.

**Why it matters.** Compounds SEC-006's information disclosure. The new edge layer is the dominant execution path (RunPod + Qwen3) for prod deployments, so this is now the most likely surface to leak a stack trace. While Jinja2 autoescape prevents XSS in the dashboard, the `/partials/jobs.html` template is not in the new code path; the API response is consumed by tooling that may render it.

**Recommendation.** Categorize `str(exc)` into a small set of public-safe buckets (`model_not_loaded`, `timeout`, `unsupported_language`, `invalid_input`, `internal`) before returning the 500. Keep `logger.exception(...)` for the full traceback. The categorization can be a small `_classify_error(exc: BaseException) -> str` function that matches on exception type and content.

**Verification.** Force a handler failure with an exception message containing a fake internal path `/runpod-volume/models/qwen3-12hz-1.7b`; assert the 500 body's error field does NOT contain the path but DOES contain one of the documented buckets. Trigger a CUDA OOM; assert the error bucket is `internal` and not the raw PyTorch text.

### SEC-013 — `RunPodPrice` sends API key as URL query parameter instead of Authorization header

```yaml
status: verified
severity: medium
effort: S
reviewed_at: dbec2be
last_verified_at:
  commit: 1fbedbc
  date: '2026-06-24'
fixed_in:
- 1fbedbc
files:
- path: src/acheron/worker_sdk/pricing.py
  lines: 179-193
related:
- OBS-006
```

**Issue.** `RunPodPrice._post_graphql` (pricing.py:185-190) sends the RunPod API key as a URL query parameter: `params={'api_key': self.api_key}`. This is the only place in the codebase that uses query-string auth — the health providers (health_providers.py:41, 72) correctly use `Authorization: Bearer` headers. RunPod's own REST API accepts both, but query-string secrets are routinely logged by HTTP access log middleware, CDN edges, and proxy layers. The edge container runs alongside the orchestrator on the same Docker network, so there is no proxy in the default path; however, the worker-side `ACHERON_WORKER__RUNPOD_BASE_URL` test seam (pricing.py:185) hints that the request can be intercepted by `stubs/_sdk_base/mock_runpod.py`, and the test seam is the only thing keeping the key out of test logs. In a future refactor that puts a real proxy in front, the key lands in the proxy's access log.

**Why it matters.** API keys in URL parameters are a documented security anti-pattern (OWASP API Security Top 10 — API3:2023 Broken Object Property Level Authorization, and the older CWE-598 'Use of GET Request with Sensitive Query Strings'). A leaked RunPod API key grants billing-API access to the RunPod account, which is worse than a leaked registration token (which is local to the orchestrator). The fix is two lines.

**Recommendation.** Replace `params={'api_key': self.api_key}` with `headers={'Authorization': f'Bearer {self.api_key}'}`. Verify against RunPod's docs that GraphQL accepts the Bearer header (it does — RunPod uses the same auth for REST and GraphQL).

**Verification.** Add a test that monkeypatches `httpx.AsyncClient.post` to capture the request; assert the params kwarg is empty AND the `Authorization` header starts with `Bearer `. Hit the real RunPod GraphQL endpoint with a known endpoint id; assert a 200 with the header-only auth shape.

### SEC-014 — `worker.edge.yaml` default `orchestrator_url` is HTTP — registration token sent in cleartext when env var is not overridden

```yaml
status: fixed
severity: medium
effort: S
reviewed_at: dbec2be
last_verified_at:
  commit: b2c8702
  date: '2026-06-25'
fixed_in: [b2c8702]
files:
- path: workers/qwen3tts/worker.edge.yaml
  lines: '11'
- path: src/acheron/worker_sdk/registration.py
  lines: 48-50
related:
- OBS-007
- SEC-003
```

**Issue.** `worker.edge.yaml` (the config baked into the acheron-worker-edge image at `Dockerfile.edge:31`) sets `orchestrator_url: 'http://orchestrator:8000'`. `docker-compose.yml:174` overrides this to https in the compose-managed deployment, but a deployer that runs the edge image standalone (e.g., on a RunPod pod or a different orchestrator topology) inherits the HTTP default. `registration.py:50` puts the bearer token in the `Authorization` header, which is transmitted in cleartext over HTTP. Any on-path observer between the edge container and the orchestrator (a Docker bridge snoop, a sidecar, an L7 proxy with request logging) captures the registration token, and the attacker can then register an arbitrary worker endpoint and exfiltrate job payloads (per the SEC-008 / SEC-009 exposure). The same default also affects the `_register` flow in `app.py:103-113`.

**Why it matters.** Baking an HTTP default into the image is the kind of 'works in dev, fails in prod' pattern SEC-003 documented for the orchestrator's TLS configuration. The orchestrator has a startup warning + opt-out flag (`ACHERON_ALLOW_INSECURE`) but the edge has no such guard — it will happily POST a bearer token to a plaintext URL. The same orchestrator in the default compose stack serves TLS, so the edge connects to `https://orchestrator:8000`; the plaintext default is the fallback path, which is exactly the path that needs the guard.

**Recommendation.** Default `orchestrator_url` to https in `worker.edge.yaml` (mirroring the orchestrator's actual deployment). If a deployer needs HTTP for dev, they can override via env var or a custom `worker.yaml`. In `settings.py`, log a WARNING at startup if `orchestrator_url` starts with `http://` and `ACHERON_ALLOW_INSECURE_REGISTRATION=1` is not set. Apply the same check in `app.py` lifespan before calling `register_with_orchestrator`.

**Verification.** Build the edge image with the default `worker.edge.yaml`, start it with no override, point it at a tcpdump-capturing orchestrator on HTTP; assert the `Authorization` header value is logged by the operator before the registration is allowed to proceed (or that the edge refuses to start with a clear error message). Override the env var to https and confirm the guard passes.

### SEC-015 — All Docker images (orchestrator, dashboard, worker-stub-base, acheron-worker-edge, qwen3tts-runpod) run as root — no `USER` directive

```yaml
status: fixed
severity: low
effort: S
reviewed_at: dbec2be
last_verified_at:
  commit: 2e035a3
  date: 2026-06-25
fixed_in: [2e035a3]
files:
  - path: Dockerfile
    lines: 1-47
  - path: Dockerfile.edge
    lines: 1-50
  - path: workers/qwen3tts/Dockerfile.runpod
    lines: 1-55
related: []
```

**Issue.** None of the three Dockerfiles create a non-root user or set `USER`. The orchestrator, dashboard, worker-stub-base, acheron-worker-edge, and qwen3tts-runpod images all run as uid 0 (root). This is a long-standing container-security baseline: the Docker daemon defaults to root, and the default `python:3.14-slim` / `python:3.12-slim` images include a working `root` user with no password. The orchestrator processes untrusted input (EPUBs, audio, job payloads from the API), the edge exposes a public `/execute` endpoint (per SEC-007), and the runpod image loads an arbitrary HuggingFace model checkpoint into a process running as root. Any code-execution vulnerability in the orchestrator's parser (e.g., an EPUB XML bomb, a soundfile deserialization issue) escalates to full host root because the container is already root.

**Why it matters.** Defense-in-depth. Running as non-root is one of the cheapest and most-effective hardening steps; missing it from a security review is unusual. For the runpod image the model loading path adds another dimension — a malicious or compromised HF checkpoint that triggers torch.load arbitrary code execution already runs as root inside the container, with the HF cache volume mounted (`Dockerfile.runpod:48`). The same applies to the volume-mounted `certs/` in compose and the `acheron-data/` volume.

**Recommendation.** Add a `RUN useradd --create-home --uid 1000 acheron` (or similar) to each stage and a `USER acheron` before the `CMD`. For stages that need to bind privileged ports (<1024), use setcap or a higher port (orchestrator already uses 8000, edge uses 8001, dashboard 8080 — all > 1024). Update healthcheck commands that run python to either run as the unprivileged user or use `gosu`. In compose, ensure bind-mounted volumes are owned by the matching uid (a `user:` directive on each service is the cleanest approach).

**Verification.** Build any of the images and `docker run --rm <image> id` — must print `uid=1000(acheron)`. Add a startup test that confirms the healthcheck command (which uses urllib) still works as the unprivileged user. For the runpod image, confirm torch can still write to the model cache dir under the unprivileged user (chown the volume on first mount).

### SEC-016 — Granite-speech edge image default `orchestrator_url` is HTTP — registration token sent in cleartext when env var is not overridden (new instance of SEC-014)

```yaml
status: fixed
severity: medium
effort: S
reviewed_at: e54458416e9bfe890a473dd9d542978d205b40a1
last_verified_at:
  commit: b2c8702
  date: '2026-06-25'
fixed_in: [b2c8702]
files:
- path: workers/granite_speech/worker.edge.yaml
  lines: '7'
related:
- SEC-014
```

**Issue.** `workers/granite_speech/worker.edge.yaml:7` sets `orchestrator_url: "http://orchestrator:8000"`. The docker-compose service overrides to https at line 208, but a deployer that runs the acheron-worker-edge image standalone (RunPod pod, different topology) inherits the HTTP default; `registration.py:50` puts the bearer token in the Authorization header, transmitted in cleartext.

**Why it matters.** Bakes an HTTP fallback into a second image. Standalone deployers inherit the bug. Token sent in cleartext over HTTP allows on-path observers to register a malicious worker endpoint and exfiltrate job payloads — same downstream impact as SEC-008/009.

**Recommendation.** Default `orchestrator_url` to `https://orchestrator:8000` in `workers/granite_speech/worker.edge.yaml:7`. In `settings.py`, log a WARNING at startup if `orchestrator_url` starts with `http://` and `ACHERON_ALLOW_INSECURE_REGISTRATION=1` is not set.

**Verification.** Build the edge image with the default `worker.edge.yaml`; confirm `worker.edge.yaml:7` reads `https://orchestrator:8000`.

### SEC-017 — Granite-speech runpod image runs as root — no `USER` directive (new instance of SEC-015)

```yaml
status: fixed
severity: low
effort: S
reviewed_at: e54458416e9bfe890a473dd9d542978d205b40a1
last_verified_at:
  commit: e447339
  date: 2026-06-25
fixed_in: [e447339]
files:
  - path: workers/granite_speech/Dockerfile.runpod
    lines: 1-65
related: [SEC-015]
```

**Issue.** `workers/granite_speech/Dockerfile.runpod` is structurally identical to `workers/qwen3tts/Dockerfile.runpod` that SEC-015 was filed against: no `RUN useradd`, no `USER` directive, the final `CMD` runs as uid 0.

**Why it matters.** Defense-in-depth regression identical to SEC-015: a malicious or compromised HF checkpoint that triggers `torch.load` arbitrary code execution (or a soundfile deserialization issue) escalates to full host root.

**Recommendation.** Add a `RUN useradd --create-home --uid 1000 acheron` step before `CMD` and `USER acheron` at the bottom. Confirm the entrypoint works as the unprivileged user.

**Verification.** Build the image; `docker run --rm <image> id` must print `uid=1000(acheron)`.

### SEC-018 — `granite-speech-edge` compose service hardcodes `:-dev-registration-token` fallback (new instance of SEC-011)

```yaml
status: verified
severity: high
effort: S
reviewed_at: e54458416e9bfe890a473dd9d542978d205b40a1
last_verified_at:
  commit: 9b4adb6
  date: 2026-06-24
fixed_in: [9b4adb6]
files:
  - path: docker-compose.yml
    lines: 209
  - path: docker-compose.yml
    lines: 242
related: [SEC-011, SEC-022]
```

**Issue.** `docker-compose.yml:209` sets `ACHERON_WORKER__REGISTRATION_TOKEN: ${ACHERON_REGISTRATION_TOKEN:-dev-registration-token}` for the new `granite-speech-edge` service. `docker-compose.yml:242` adds the same `:-dev-registration-token` fallback for the new `translategemma-edge` service in 8c. If the operator forgets to set `ACHERON_REGISTRATION_TOKEN` in their `.env`, both new services ship with the publicly-known `dev-registration-token`.

**Why it matters.** Re-introduces the SEC-011 dev-default bypass in a second (now third, via SEC-022) compose service. Registering a malicious worker against the orchestrator allows an attacker to receive ASR + translation job payloads and to consume the RunPod credits.

**Recommendation.** Remove the `:-dev-registration-token` fallback in `docker-compose.yml:209` so the env var is required. Make `.env.example:7` document the variable with a placeholder and a comment instructing the operator to generate one with `openssl rand -hex 32`. The orchestrator's startup should fail closed if the env var is the empty string.

**Verification.** `docker compose --profile runpod-asr up` with no `.env`; assert refusal.

### SEC-019 — Edge `/execute` multipart branch returns 500 body with `error=str(exc)`, exposing raw exception detail (new instance of SEC-012)

```yaml
status: fixed
severity: low
effort: S
reviewed_at: 77aadcd
last_verified_at:
  commit: bb9ab27
  date: 2026-06-26
fixed_in: ["bb9ab27"]
files:
  - path: src/acheron/worker_sdk/_edge_http.py
    lines: 355
related: [SEC-012, B08, B19]
```

**Issue.** `_run_execute_multipart` (lines 167-186) catches `WorkerError` from `_parse_multipart_request` and returns a JSON 500 body built from `JobResult(... error=str(exc) ...)`. Internal exception detail (parse-failure messages naming internal paths, boundary mismatches, content-type strings) flows through to `GET /jobs/{id}`.

**Why it matters.** Compounds SEC-012's information disclosure. The ASR multipart branch is the dominant new code path.

**Recommendation.** Categorize `str(exc)` into a small set of public-safe buckets before returning the 500. Keep `logger.exception(...)` for the full traceback.

**Verification.** Force a multipart parse failure; assert the 500 body's `error` field is one of the documented buckets, not the raw `BytesParser` error string.

### OBS-009 — `granite-speech-edge` service exposes `/execute` on host port 8008 — unauthenticated (new instance of OBS-007)

```yaml
status: verified
severity: medium
effort: S
reviewed_at: e54458416e9bfe890a473dd9d542978d205b40a1
last_verified_at:
  commit: fa87bc6
  date: 2026-06-24
fixed_in: [fa87bc6]
files:
  - path: docker-compose.yml
    lines: 200-231, 233-265
  - path: docker-compose.yml
    lines: 200-231, 233-265
  - path: src/acheron/worker_sdk/_edge_http.py
    lines: 156-163, 167-186, 233-271
related: [OBS-007, OBS-010]
```

**Issue.** `docker-compose.yml:204` maps `8008:8001` for `granite-speech-edge`, exposing the edge's unauthenticated POST `/execute` on the host network. 8c adds the same pattern at `docker-compose.yml:238` (translategemma-edge on `8009:8001`) — see OBS-010. Any host-side process can call `/execute` directly, bypassing the orchestrator's job-submission path.

**Why it matters.** The `/execute` endpoint is the entire cost-bearing surface of the RunPod edge — a single host-level access yields a billable surface, a probe for the RunPod endpoint_id via timing/exception response, and a free proxy to the RunPod serverless endpoint.

**Recommendation.** Require an `Authorization: Bearer <registration_token>` dependency on `/execute`, and gate the port to `expose: [8001]` instead of `ports:` in compose so it is not host-reachable.

**Verification.** `curl -X POST http://localhost:8008/execute -d '{...}'` from the host should be rejected (401/403).

### PERF-008 — `HttpWorker._post_multipart` constructs a new `httpx.AsyncClient` per call (new instance of PERF-007)

```yaml
status: stale
severity: low
effort: S
reviewed_at: e54458416e9bfe890a473dd9d542978d205b40a1
last_verified_at:
  commit: a9298e0473399a3db86a33b164f0cf6263834195
  date: 2026-06-24
fixed_in: []
files:
  - path: src/acheron/shell/transports/http.py
    lines: 105-123
related: [PERF-007]
```

**Issue.** The new `_post_multipart` method (transports/http.py:143-165) follows the same pattern PERF-007 flagged: when `self._client is None` (the common case), the method opens a throwaway `httpx.AsyncClient` per call. The pre-existing `_request` method at lines 73-74 has the same anti-pattern.

**Why it matters.** Per-call `httpx.AsyncClient` defeats keep-alive and forces a full handshake on every request. For a long-running ASR plan with N steps, that's N handshakes to the edge per job.

**Recommendation.** Reuse the existing `self._client` injection seam on `_post_multipart` (the `if self._client is not None` branch is already in place at line 151-152). At the `default_worker_factory` level, build a shared `httpx.AsyncClient` per `worker_id` and pass it to the constructed `HttpWorker`.

**Verification.** Run a 10-step ASR plan against a fake edge that records connection setup; assert the counter increments by 1 (one per worker_id), not 20.

## SEC (8c delta)

### SEC-020 — Translategemma Dockerfile.runpod runs as root — no USER directive (new instance of SEC-015/SEC-017)

```yaml
status: verified
severity: low
effort: S
reviewed_at: eb6849c85d83f2277eb450f18a11e63cae2defd1
last_verified_at:
  commit: 5c8fd8a
  date: 2026-06-24
fixed_in: [5c8fd8a]
files:
  - path: workers/translategemma/Dockerfile.runpod
    lines: 1-61
related: [SEC-015, SEC-017]
```

**Issue.** `workers/translategemma/Dockerfile.runpod` is structurally identical to `workers/qwen3tts/Dockerfile.runpod` and `workers/granite_speech/Dockerfile.runpod`: no `RUN useradd`, no `USER` directive, the final `CMD ["python", "runpod_entrypoint.py"]` runs as uid 0. This is the third image in the fleet to ship root-by-default (qwen3tts, granite-speech, translategemma).

**Why it matters.** Defense-in-depth regression identical to SEC-015/SEC-017. The TranslateGemma 12B model is loaded into VRAM by a process running as root; a malicious or compromised HF checkpoint that triggers arbitrary code execution at `from_pretrained` time (or a transformers deserialization issue) escalates to full host root. With the HF cache volume mounted and `HF_HUB_OFFLINE=1` set, the attack surface is bounded to the pre-baked snapshot, but the container still has root access to the host's network and any shared bind mounts.

**Recommendation.** Add `RUN useradd --create-home --uid 1000 acheron` before `CMD` and `USER acheron` at the bottom of `workers/translategemma/Dockerfile.runpod`. Update `Dockerfile.runpod:43` (`WORKDIR /app`) so the directory is owned by the unprivileged user, or run `chown -R acheron:acheron /app` before the `USER` switch. Update the entrypoint to use `gosu` or `setpriv` if any prior step requires root. Apply the same fix uniformly to the qwen3tts and granite-speech images (SEC-015, SEC-017).

**Verification.** Build the image and `docker run --rm <image> id` — must print `uid=1000(acheron)`. Add a CI gate that runs the same `id` check on all three RunPod images.

### SEC-021 — Translategemma worker.edge.yaml default `orchestrator_url` is HTTP — registration token sent in cleartext (new instance of SEC-014/SEC-016)

```yaml
status: verified
severity: medium
effort: S
reviewed_at: eb6849c85d83f2277eb450f18a11e63cae2defd1
last_verified_at:
  commit: 63fc3f9
  date: 2026-06-24
fixed_in: [63fc3f9]
files:
  - path: workers/translategemma/worker.edge.yaml
    lines: 7
related: [SEC-014, SEC-016]
```

**Issue.** `workers/translategemma/worker.edge.yaml:7` sets `orchestrator_url: "http://orchestrator:8000"`. The docker-compose service overrides to https at line 241, but a deployer that runs the acheron-worker-edge image standalone (RunPod pod, different topology) inherits the HTTP default. `registration.py:50` puts the bearer token in the Authorization header, transmitted in cleartext over HTTP. This is the third edge config to ship the HTTP default (qwen3tts, granite-speech, translategemma).

**Why it matters.** Bakes an HTTP fallback into a third image. Standalone deployers inherit the bug. Token sent in cleartext over HTTP allows on-path observers to register a malicious worker endpoint and exfiltrate job payloads (EPUB chapters, chunks.json, translated text) — same downstream impact as SEC-008/009.

**Recommendation.** Default `orchestrator_url` to `https://orchestrator:8000` in `workers/translategemma/worker.edge.yaml:7`. In `settings.py`, log a WARNING at startup if `orchestrator_url` starts with `http://` and `ACHERON_ALLOW_INSECURE_REGISTRATION=1` is not set. Apply the same fix to `workers/qwen3tts/worker.edge.yaml` and `workers/granite_speech/worker.edge.yaml` (SEC-014, SEC-016).

**Verification.** Build the edge image with the default worker.edge.yaml; confirm line 7 reads `https://orchestrator:8000`. Add a startup-test that boots the edge with the HTTP default and asserts a clear warning is logged or the edge refuses to start.

### SEC-022 — `translategemma-edge` compose service hardcodes `${ACHERON_REGISTRATION_TOKEN:-dev-registration-token}` fallback (new instance of SEC-011/SEC-018)

```yaml
status: verified
severity: high
effort: S
reviewed_at: eb6849c85d83f2277eb450f18a11e63cae2defd1
last_verified_at:
  commit: 9b4adb6
  date: 2026-06-24
fixed_in: [9b4adb6]
files:
  - path: docker-compose.yml
    lines: 242
related: [SEC-011, SEC-018]
```

**Issue.** `docker-compose.yml:242` sets `ACHERON_WORKER__REGISTRATION_TOKEN: ${ACHERON_REGISTRATION_TOKEN:-dev-registration-token}` for the new translategemma-edge service. If the operator forgets to set `ACHERON_REGISTRATION_TOKEN` in their `.env`, the new service ships with the publicly-known `dev-registration-token` (documented in .env.example:7). This is the third compose service to use the same dev-default bypass (orchestrator, qwen3tts-edge, granite-speech-edge, translategemma-edge).

**Why it matters.** Re-introduces the SEC-011 dev-default bypass in a fourth compose service. Registering a malicious worker against the orchestrator allows an attacker to receive TRANSLATION job payloads (chunks.json containing chapter text) and to consume the RunPod credits via fabricated /execute calls. The blast radius is now 4 compose services (orchestrator + 3 edge workers) all defaulting to the same publicly-known token.

**Recommendation.** Remove the `:-dev-registration-token` fallback in `docker-compose.yml:242` (and the 3 pre-existing sites at lines 35, 95, 175, 209) so the env var is required. Make `.env.example:7` document the variable with an empty placeholder and a comment instructing the operator to generate one with `openssl rand -hex 32`. The orchestrator's startup should fail closed if the env var is the empty string. Add a CI / startup check that refuses to boot if the token equals `dev-registration-token` or is shorter than 32 chars.

**Verification.** `docker compose --profile runpod-translation up` with no `.env`; assert refusal. Run with `ACHERON_REGISTRATION_TOKEN=dev-registration-token`; assert the same refusal. Run with a freshly generated 32-char token; assert registration succeeds.

### SEC-023 — Translategemma edge `phantom_handler` import path requires `workers/translategemma/handler.py` on PYTHONPATH, but `Dockerfile.edge` does not copy it — edge service is broken by design

```yaml
status: verified
severity: high
effort: S
reviewed_at: eb6849c85d83f2277eb450f18a11e63cae2defd1
last_verified_at:
  commit: fd3bcc1
  date: 2026-06-24
fixed_in: [fd3bcc1]
files:
  - path: Dockerfile.edge
    lines: 39-41
related: [DOC-005]
```

**Issue.** `workers/translategemma/worker.edge.yaml:12` sets `phantom_handler: "workers.translategemma.handler:TranslateGemmaRunpodHandler"`, which the acheron-worker-edge SDK resolves at boot to read the worker's static capabilities. `Dockerfile.edge` (unchanged in this delta, lines 26-37) copies only `workers/qwen3tts/{__init__.py,handler.py,worker.edge.yaml}` and `workers/granite_speech/{__init__.py,handler.py,worker.edge.yaml}` — it does NOT copy `workers/translategemma/handler.py`, `workers/translategemma/__init__.py`, or `workers/translategemma/worker.edge.yaml`. The translategemma-edge compose service (`docker-compose.yml:233`) sets `WORKER_NAME: translategemma`, which makes the CLI look for `/app/translategemma.worker.yaml` (also not bundled). The edge container will fail to start with `ModuleNotFoundError: workers.translategemma.handler` or `FileNotFoundError: translategemma.worker.yaml`.

**Why it matters.** This is a critical availability issue for the new service: the `translategemma-edge` profile is documented in the new README (`workers/translategemma/README.md`) as the deploy path, but the Dockerfile.edge delta was omitted. The SEC concern is that a deployer who follows the README will get a broken service, attempt to debug, and may weaken security (e.g. disable auth, open the port wider) to make it work. Filing under SEC because the broken-by-design state creates pressure to misconfigure the service.

**Recommendation.** Add to `Dockerfile.edge` (between line 37 and the ENV): `COPY workers/translategemma/__init__.py /app/workers/translategemma/__init__.py`, `COPY workers/translategemma/handler.py /app/workers/translategemma/handler.py`, `COPY workers/translategemma/worker.edge.yaml /app/translategemma.worker.yaml`. Update the README deployer guide to confirm the edge image build. Add a CI check that boots the translategemma-edge container and asserts the phantom_handler imports cleanly.

**Verification.** Build the edge image with the Dockerfile.edge update, run `docker compose --profile runpod-translation up translategemma-edge`, and assert the container starts and registers with the orchestrator (curl `/workers` to confirm `translategemma-edge` appears in the list).

## OBS (8c delta)

### OBS-010 — `translategemma-edge` service exposes `/execute` on host port 8009 — unauthenticated (new instance of OBS-007/OBS-009)

```yaml
status: verified
severity: medium
effort: S
reviewed_at: eb6849c85d83f2277eb450f18a11e63cae2defd1
last_verified_at:
  commit: fa87bc6
  date: 2026-06-24
fixed_in: [fa87bc6]
files:
  - path: docker-compose.yml
    lines: 233-265
related: [OBS-007, OBS-009, SEC-022]
```

**Issue.** `docker-compose.yml:238` maps `8009:8001` for the new translategemma-edge service, exposing the edge's unauthenticated POST `/execute` on the host network. The new compose service follows the exact pattern OBS-007 (qwen3tts-edge on 8004) and OBS-009 (granite-speech-edge on 8008) flagged: a host-side process can call `/execute` directly, bypassing the orchestrator's job-submission path. Any host-level access yields a billable surface, a probe for the RunPod endpoint_id via timing/exception response, and a free proxy to the RunPod serverless endpoint with the deployer's own API key.

**Why it matters.** This is the third edge service in the fleet to ship with the unauthenticated-host-port anti-pattern. The /execute endpoint is the entire cost-bearing surface of the RunPod edge — a single host-level access to port 8009 yields a RunPod bill. The dev-registration-token fallback (SEC-022) compounds the risk: a misconfigured deployment that ships the default token has the /execute endpoint also reachable from the host network.

**Recommendation.** Require an `Authorization: Bearer <registration_token>` dependency on `/execute` that mirrors the orchestrator's `verify_registration_token`, and gate the port to `expose: [8001]` instead of `ports:` in compose so it is not host-reachable. Apply the same fix to the qwen3tts-edge (8004) and granite-speech-edge (8008) services. Update the worker_sdk._edge_http._run_execute to enforce the auth dependency.

**Verification.** `curl -X POST http://localhost:8009/execute -d '{...}'` from the host should be rejected (401/403). For the auth fix, assert the Authorization header is required and a missing header produces 401. With `expose:` instead of `ports:`, re-run the curl from the host — it should fail with a connection refused.

### OBS-011 — `validate_chunking_fits_workers` runs in `submit_job` with no log on success or failure — operator cannot confirm the plan-time input-budget check ran

```yaml
status: fixed
severity: low
effort: S
reviewed_at: eb6849c85d83f2277eb450f18a11e63cae2defd1
last_verified_at:
  commit: f1b8364
  date: 2026-06-25
fixed_in:
- f1b8364
files:
- path: src/acheron/core/planner.py
  lines: 122-172
related:
- TEST-015
- ARCH-019
- CFG-009
```

**Issue.** The new `validate_chunking_fits_workers(...)` call sits between `compile_plan` (orchestrator.py:244) and `Plan compiled for %s` log (line 251). On success the call is silent; on failure the ChunkingTooLongForWorkerError bubbles up to the FastAPI exception handler with no specific log line at the submit_job level naming the worker type, the chunking max, the max_input_tokens, or the chars_per_token estimate. Operators have no log evidence that the input-budget check ran, and no way to distinguish a chunking-too-long failure from a generic plan compile failure without parsing the API error body.

**Why it matters.** Compounds the existing free-form logging gap flagged in OBS-003. The validation is the primary safety net against the chunking-bigger-than-token-limit misconfiguration (e.g. someone bumps `max_chunk_length` to 10000 without realising the translategemma worker's `max_input_tokens=2048` truncates the rest). When the check fires (rare, but high-stakes), the operator needs to see the worker type and the conflicting config values in the log to diagnose the misconfiguration. A 400 response with the same values is also emitted by the API, but the orchestrator log is the primary record.

**Recommendation.** Wrap the `validate_chunking_fits_workers` call with a `logger.info` on success (`Plan input-budget validated for %s: max_chunk_length=%d, text-input workers checked=%d`, job_id, max_chunk_length, n_workers) and a `logger.warning` on the failure path before re-raising. Alternative: move the validation into `compile_plan` so the existing `Plan compiled for %s` log emits only on full success, and the PlanError propagates with the existing log machinery.

**Verification.** Submit a job with `max_chunk_length=10000` against a translategemma worker (max_input_tokens=2048); assert the orchestrator log contains a line naming `translategemma`, `max_chunk_length=10000`, and `max_input_tokens=2048` before the API returns 400.

### OBS-012 — Multipart parse-failure path in `_run_execute_multipart` returns 500 with no `logger.exception` — operator has no log evidence of parse failures

```yaml
status: verified
severity: low
effort: S
reviewed_at: 77aadcd
last_verified_at:
  commit: pending
  date: 2026-07-23
fixed_in: ["pending"]
files:
  - path: src/acheron/worker_sdk/_edge_http.py
    lines: 338-364
related: [OBS-006, OBS-005]
```

**Issue.** `_run_execute_multipart` (lines 335-361) catches `(WorkerError, ValueError, KeyError)` and builds a `JobResult(... error=str(parser_error) ...)` 500 response, but unlike the dispatch path at line 395 (`logger.exception("%s handler failed for job %s", ...)`) it emits no log line. The file has exactly one `logger.exception` call (line 395); the multipart parser path adds zero observability. A malformed multipart body submitted from the orchestrator shows up only in the response body of the failing call — there is no `logger.warning`/`logger.exception` so the operator cannot diagnose the parse failure from logs alone, only by correlating against a 500 response that may or may not propagate to `GET /jobs/{id}`. This is the same anti-pattern flagged in OBS-006 and OBS-005 (silently swallowed errors).

**Why it matters.** Compounds the structured-logging gap flagged in OBS-003. The 8b/8c ASR/TRANSLATION path is the dominant new code path; parse failures (boundary mismatches, unsupported Content-Type headers, missing envelope JSON) are the most common client-side error class. Without a log line at the edge, the operator has to infer the cause from the orchestrator's downstream PlanResult.errors string, which is now sanitised (per SEC-006) and so has lost diagnostic detail.

**Recommendation.** Before the `return JSONResponse(...)` at line 360, emit `logger.exception("Edge multipart parse failed: %s", parser_error)` (mirroring the dispatch path's tone). Use `logger.exception` (not `warning`) so the cause chain is preserved; the response body still carries the sanitised message via the existing `error=str(parser_error)` field.

**Verification.** Submit a malformed multipart body (e.g., wrong boundary, missing envelope) to `/execute`; assert the edge logs an exception with the parse failure cause. The response body should still be the 500 with `error=...`. Add a regression test in `tests/worker_sdk/test_edge_http_multipart.py` that injects a malformed body, captures `caplog.records`, and asserts at least one `logging.ERROR`/`logging.EXCEPTION` record is emitted with the parse error context.

### OBS-013 — `Orchestrator._drain_inflight_tasks` is silent — no log on entry, completion, or 5s timeout firing

```yaml
status: verified
severity: medium
effort: S
reviewed_at: 59458ba
last_verified_at:
  commit: 0635bfb
  date: 2026-07-22
fixed_in: ["pending"]
files:
  - path: src/acheron/shell/orchestrator.py
    lines: 263-278
related: [OBS-001, OBS-003, OBS-005]
```

**Issue.** The new `_drain_inflight_tasks` (orchestrator.py:263-278, added in OBS-001 fix `8f54443`) is the hot shutdown-time path that reconciles in-flight `_execute` tasks. It cancels `self._tasks`, awaits them inside `async with asyncio.timeout(5.0):`, and returns silently on either success or timeout. There is no log when the drain starts (operators have no visibility into how many tasks are being cancelled), no log when the drain completes (no visibility into how long it took), and no log when the 5s grace timeout fires. The `asyncio.timeout(5.0)` context manager raises `TimeoutError` from its `__aexit__` when the deadline elapses; that `TimeoutError` is not caught and propagates through `shutdown()` to the FastAPI lifespan, which may abort the orchestrator's shutdown sequence without any orchestrator-level log line to explain why.

**Why it matters.** A noisy failure mode (drain takes >5s) now fails silently: the lifespan logs whatever it logs about an unhandled `TimeoutError`, but there is no orchestrator-level log that says "drained N tasks in M seconds" or "drain grace timeout fired, X tasks may not have reconciled". The OBS-001 fix added the cancel-and-await machinery but did not add the observability the OBS-003 free-form-logging gap originally called for. Combined with the unhandled `TimeoutError` in the lifespan, the operator gets a shutdown failure with no breadcrumb leading back to the drain — and the same scenario OBS-001 was supposed to make safe (a slow Redis put during shutdown) is now invisible to the operator.

**Recommendation.** Wrap the drain in try/except: log `logger.info('Draining %d in-flight _execute tasks (grace=5.0s)', len(pending))` on entry; on success log `logger.info('Drained %d tasks in %.2fs', len(pending), elapsed)`; on `TimeoutError` log `logger.warning('Drain grace timeout (5.0s) fired with %d tasks still pending; persisted state may be inconsistent', still_pending)` and either continue with the still-pending tasks (let the event loop reap them) or set their status to FAILED via `_job_store.put` before re-raising. This mirrors the convention the OBS-005 health-provider fix adopted for distinguishing failure modes. Pair with the `shutdown_drain_seconds` settings field proposed in CFG-013 so the 5.0s magic number is configurable.

**Verification.** Add a test in `tests/shell/test_orchestrator.py` that registers an `_execute` task that sleeps 10s, calls `orchestrator.shutdown()`, captures `caplog.records`, and asserts (a) an INFO line naming the task count is emitted on entry, (b) a WARNING line naming the timeout is emitted on `TimeoutError`, (c) `shutdown()` raises `TimeoutError` (preserved behaviour) but the operator-visible logs are present. Also add the success-path assertion with a fast-completing task to confirm the completion log is emitted.

### PERF-009 — Cache invalidation can close HTTP clients used by active jobs

```yaml
status: verified
severity: medium
effort: M
reviewed_at: c53da1d
last_verified_at:
  commit: pending
  date: 2026-07-23
fixed_in: [pending]
files:
  - path: src/acheron/shell/step_handler.py
    lines: 143-179
  - path: src/acheron/shell/orchestrator.py
    lines: 439-443
related: [CORR-041, OBS-014]
```

**Issue.** `submit_job()` invalidates and closes cached `HttpWorker` clients before starting the new task, even though existing jobs may still be using those shared instances.

**Recommendation.** Defer closure until active references drain, or use generation/ref-counted worker pools.

### OBS-014 — Shutdown can close the store before post-timeout reconciliation completes

```yaml
status: verified
severity: medium
effort: M
reviewed_at: c53da1d
last_verified_at:
  commit: pending
  date: 2026-07-23
fixed_in: [pending]
files:
  - path: src/acheron/shell/orchestrator.py
    lines: 215, 493-525
  - path: src/acheron/shell/api/app.py
    lines: 38-42
related: [CORR-038, CFG-013]
```

**Issue.** After the drain timeout, reconciliation writes continue in the background, while the FastAPI lifespan immediately calls `close()`. The bounded grace period can expire before a slow write completes, and Redis can then close underneath it.

**Why it matters.** A job can remain persisted as `RUNNING` even though shutdown attempted reconciliation, with no reliable terminal state for the next process.

**Recommendation.** Keep the store alive until reconciliation completes, or persist terminal failure synchronously before closing the backend.

### PERF-010 — Worker retirement cleanup scans every active job on every release

```yaml
status: open
severity: medium
effort: M
reviewed_at: e0246e0
last_verified_at:
  commit: e0246e0
  date: 2026-07-23
fixed_in: []
files:
  - path: src/acheron/shell/step_handler.py
    lines: 161-179
  - path: src/acheron/shell/orchestrator.py
    lines: 438-442
related: [PERF-009]
```

**Issue.** Every completed execution calls `release_job()`, which rebuilds `active_workers` by scanning every worker-instance list in `_job_worker_instances`. With many concurrent jobs, repeated releases perform quadratic reference scans even when no retired worker can be closed.

**Why it matters.** Job completion cleanup is on the execution path, so concurrency can turn worker retirement into avoidable event-loop latency.

**Recommendation.** Maintain per-worker reference counts or a reverse worker-to-job index so release updates only affected instances.

**Verification.** Instrument cleanup for many concurrent jobs and assert total reference work grows linearly rather than quadratically.

### PERF-011 — Health monitor retains BOOTING timestamps for removed workers

```yaml
status: fixed
severity: medium
effort: S
reviewed_at: e0246e0
last_verified_at:
  commit: 72b1668
  date: 2026-07-23
fixed_in: [pending]
files:
  - path: src/acheron/shell/health.py
    lines: 98-99
  - path: src/acheron/shell/health.py
    lines: 183-198
related: [CORR-012, CORR-044]
```

**Issue.** `_booting_since` is removed on healthy or non-BOOTING outcomes, but not when a worker exceeds the BOOTING timeout and is removed by `record_health_failure()`.

**Why it matters.** Worker churn can grow health-monitor state without bound, and a re-registered worker ID can reuse a stale timestamp and be treated as already expired.

**Recommendation.** Remove the timestamp when the registry reports removal and key the timer by the current registration identity where needed.

**Verification.** Repeatedly timeout and remove workers, assert `_booting_since` remains bounded, then re-register an old ID and verify a fresh BOOTING interval.

### OBS-015 — Shutdown waits indefinitely for background persistence tasks

```yaml
status: verified
severity: medium
effort: S
reviewed_at: e0246e0
last_verified_at:
  commit: 4991e4f
  date: 2026-07-23
fixed_in: [pending]
files:
  - path: src/acheron/shell/orchestrator.py
    lines: 206-215
  - path: src/acheron/shell/orchestrator.py
    lines: 492-525
related: [OBS-014, CORR-042]
```

**Issue.** `Orchestrator.close()` calls `_wait_for_background_persists()` with `max_wait=None`. A stalled shielded reconciliation write can therefore block close indefinitely.

**Why it matters.** A slow or unavailable persistence backend can prevent the FastAPI lifespan from completing shutdown and offers no bounded recovery path.

**Recommendation.** Restore a bounded final wait, log unresolved job IDs, and define whether writes are cancelled or handed to durable reconciliation after the deadline.

**Verification.** Use a `JobStore` whose `put()` never completes, invoke `close()`, and assert it returns within the configured shutdown bound with an unresolved-write warning.
