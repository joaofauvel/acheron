---
branch: chore/code-review-update
initial_review_commit: 23c29e1
last_updated_commit: 63faed4
last_staleness_scan:
  commit: 63faed4
  date: 2026-06-21
---

# Operations

## PERF — Performance

**Grade:** B

PERF-001, PERF-002, PERF-003 remain verified. Two new medium PERF findings from Layer 11: PERF-004 (post-probe bookkeeping in `_check_all` is serial even though the probes are concurrent), PERF-005 (provider status checks in `_handle_failure` are serial and can starve the health interval).

### PERF-001 — Health checks run sequentially, blocking the whole sweep on slow/dead workers

```yaml
status: verified
severity: medium
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: d0b739b
  date: 2026-06-20
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
  commit: be7b3ab
  date: 2026-06-20
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
  commit: be7b3ab
  date: 2026-06-20
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
status: open
severity: medium
effort: S
reviewed_at: 63faed4
last_verified_at:
  commit: 63faed4
  date: 2026-06-21
fixed_in: []
files:
  - path: src/acheron/shell/health.py
    lines: 113-131
related: [PERF-001]
```

**Issue.** After the concurrent probe `asyncio.gather` (health.py:118-121), `_check_all` iterates `for worker, result in zip(...)` and awaits each `record_health_success` (health.py:129) or `_handle_failure` (health.py:131) one at a time. For the Redis backend each call is 1-2 round-trips; with W workers that's W sequential awaits. PERF-001 made the probes concurrent but left the post-probe bookkeeping serial, so the overall sweep time on Redis is now dominated by W * (Redis RTT) + failure-handling latency.

**Why it matters.** Sweep latency is now W * ~2ms for a 10-worker fleet on a 1ms-RTT Redis. The health-monitor interval is 30s; if bookkeeping overruns the interval, the next sweep is delayed (the existing `await asyncio.sleep(self._interval)` happens after the loop returns), creating observable drift. More importantly, the inter-request serial pattern prevents the monitor from absorbing fleet growth: doubling the worker count doubles the sweep time even though the probe cost was constant.

**Recommendation.** Wrap the per-worker result handling in `asyncio.gather(*(self._handle_result(worker, result) for worker, result in zip(workers, results, strict=True)), return_exceptions=True)`. Hoist the `record_health_success` / `set_worker_status` / `record_health_failure` triad into a single helper so it can be `gather`-ed. The provider-check inside `_handle_failure` is already a single await, so no other change is needed for the gather to parallelize.

**Verification.** Instrument `_check_all` with a wall-clock timer. With 20 fake workers and a fake Redis that adds 2ms per call, assert the post-probe phase completes in <5ms (parallel) rather than >40ms (serial). Add a test that mocks 20 workers, intercepts `record_health_success`, and asserts the calls overlap (e.g. via call timestamps).

### PERF-005 — Provider status checks in _handle_failure run sequentially and can starve the health interval

```yaml
status: open
severity: medium
effort: S
reviewed_at: 63faed4
last_verified_at:
  commit: 63faed4
  date: 2026-06-21
fixed_in: []
files:
  - path: src/acheron/shell/health.py
    lines: 133-149
related: [PERF-004]
```

**Issue.** `_handle_failure` (health.py:133-149) awaits `provider.check_status(endpoint_id)` synchronously for each failing worker. `RunPodHealthProvider.check_status` and `HuggingFaceHealthProvider.check_status` (health_providers.py:39-94) each have a 10s httpx timeout. The caller iterates failures one at a time inside `_check_all` (health.py:130-131). With N concurrent failures that all have a provider configured, the failure-handling phase can take N * 10s — already exceeding the 30s default interval at N=4, and there is no back-pressure to drop provider calls when the budget is consumed.

**Why it matters.** A platform-side outage (e.g. all RunPod workers go down at once) causes `_check_all` to block for tens of seconds, slipping the next interval and delaying detection of *new* health transitions for the *other* workers. This is the same class of regression PERF-001 fixed for HTTP probes: a slow dependent on a small number of unhealthy workers serializes the whole sweep. The new layer added by Layer 11 re-introduces the failure-mode in a different subsystem.

**Recommendation.** Either (a) `asyncio.gather` the `provider.check_status` calls across the failure batch, applying a per-batch budget (e.g. 5s overall) and defaulting to OFFLINE on timeout; or (b) use a shorter per-call timeout (3-5s) and an overall `asyncio.wait_for` ceiling; or (c) skip the provider check entirely once a worker is already known-OFFLINE in the local store to avoid redundant remote calls. Combine with PERF-004 to also parallelize the surrounding `record_health_success`/`record_health_failure` pipeline calls.

**Verification.** Register 5 workers all pointing at a fake provider endpoint that sleeps 10s before responding. Measure the time `_check_all` spends in failure handling: must be <5s (gathered) rather than >50s (serial). Add a regression test using a fake provider with controllable latency.

## OBS — Observability

**Grade:** B

OBS-001 (shutdown drain) and OBS-003 (structured logging) remain open. OBS-002 and OBS-004 remain verified. One new medium OBS finding: OBS-005 — health providers swallow `(httpx.HTTPError, OSError)` exceptions silently with no diagnostic log, masking configuration mistakes like an unset `${HF_API_KEY}`.

### OBS-001 — Shutdown does not drain in-flight _execute tasks; cancelled jobs stay stuck at "running"

```yaml
status: open
severity: medium
effort: M
reviewed_at: 23c29e1
last_verified_at:
  commit: 63faed4
  date: 2026-06-21
fixed_in: []
files:
  - path: src/acheron/shell/orchestrator.py
    lines: 202-210
  - path: src/acheron/shell/orchestrator.py
    lines: 253-255
  - path: src/acheron/shell/orchestrator.py
    lines: 259-347
related: [OBS-004]
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
  commit: be7b3ab
  date: 2026-06-20
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
status: open
severity: low
effort: L
reviewed_at: 23c29e1
last_verified_at:
  commit: 63faed4
  date: 2026-06-21
fixed_in: []
files:
  - path: src/acheron/shell/orchestrator.py
    lines: 230-237
  - path: src/acheron/shell/health.py
    lines: 113-152
  - path: src/acheron/shell/step_handler.py
    lines: 123
  - path: dashboard/app.py
    lines: 27
related: []
```

**Issue.** All logging uses free-form `%s` format strings (e.g. `orchestrator.py:230-237`). There is no structured/JSON logging and no correlation token beyond job_id appearing inside message text. The Layer 11 diff adds more free-form `logger.warning`/`logger.info` calls in `health.py` and `orchestrator.py`, not structured. In a distributed system with concurrent jobs, workers, and health checks, correlating a failure across orchestrator→transport→worker requires grepping free-text rather than filtering on fields.

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
  commit: be7b3ab
  date: 2026-06-20
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
status: open
severity: medium
effort: S
reviewed_at: 63faed4
last_verified_at:
  commit: 63faed4
  date: 2026-06-21
fixed_in: []
files:
  - path: src/acheron/shell/health_providers.py
    lines: 49-50
  - path: src/acheron/shell/health_providers.py
    lines: 80-81
related: [CORR-010, EXC-003]
```

**Issue.** Both `RunPodHealthProvider.check_status` (health_providers.py:49-50) and `HuggingFaceHealthProvider.check_status` (health_providers.py:80-81) catch `(httpx.HTTPError, OSError)` and silently return `WorkerStatus.OFFLINE`. The blanket `except` erases the distinction between (a) provider API key is invalid/wrong (401/403), (b) provider is rate-limiting (429), (c) network is down, (d) endpoint_id does not exist (404), and (e) provider service is degraded (5xx). The caller in `health._handle_failure` does log a warning when the provider itself raises (health.py:142), but the providers' `except` block short-circuits before that path is reached, so the user has no log evidence of the actual failure mode.

**Why it matters.** When all HF workers are reported BOOTING (false positive) or OFFLINE (false negative) the operator cannot diagnose whether the orchestrator is misconfigured (wrong API key) or the platform is the problem. The fallback to OFFLINE on auth failure is especially bad: a typo'd `${HF_API_KEY}` will silently mark every HF-endpoint worker as OFFLINE on every health cycle, with no log line pointing at the cause. This compounds the `${VAR}` silent-fail in `config._expand_env_vars` (config.py:18-26) — if the env var is unset, the provider is not even instantiated, also with no warning.

**Recommendation.** In each provider's `except (httpx.HTTPError, OSError) as exc:` block, emit a structured warning: `logger.warning("%s health check for %s failed: %s", self.__class__.__name__, endpoint_id, exc)` before returning OFFLINE. Differentiate 401/403 from 5xx via the response status code (the `resp` object is in scope before the `return` in the 4xx/5xx branch on health_providers.py:53 / 82-83). Also log a warning at `create_health_providers` (health_providers.py:108-114) when an expected `api_key` is empty after env-var expansion.

**Verification.** Configure `providers.huggingface.api_key: "${HF_API_KEY}"` and leave the env var unset: orchestrator startup should log a warning naming the missing provider. Set a deliberately wrong `HF_API_KEY` and force a worker to enter the failure path: assert the log line includes the HTTP 401/403 status and the provider name.

## SEC — Security

**Grade:** C

SEC-001 through SEC-004 remain verified. SEC-005, SEC-006, SEC-007 remain open. **One new critical finding: SEC-008 — the auto-generated registration token is logged in plaintext at startup, partially undoing the SEC-002 mitigation.** One new high finding: SEC-009 — the registration token file is written without an explicit `chmod 0o600` (the same anti-pattern that SEC-001 flagged for CA private keys). One new low finding: SEC-010 — the new `last_error` field on `/workers` is exposed via the unauthenticated endpoint and embeds internal exception detail.

### SEC-001 — Dev cert private keys written world-readable (mode 0644)

```yaml
status: verified
severity: medium
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: d0b739b
  date: 2026-06-20
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
  commit: d0b739b
  date: 2026-06-20
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
  commit: d0b739b
  date: 2026-06-20
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
  commit: be7b3ab
  date: 2026-06-20
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
status: open
severity: low
effort: M
reviewed_at: 23c29e1
last_verified_at:
  commit: d0b739b
  date: 2026-06-20
fixed_in: []
files:
  - path: src/acheron/shell/api/routes/jobs.py
    lines: 20-69
  - path: src/acheron/shell/api/routes/capabilities.py
    lines: 13-23
related: []
```

**Issue.** Only the worker-registration route injects `RegistrationTokenDep` (workers.py:22). Job submission (jobs.py:21), job listing (jobs.py:66), get_job, and capabilities (capabilities.py:14) have no auth dependency at all, so any network-reachable client can submit jobs (consuming worker resources/cost) and enumerate worker endpoints. The `X-Forwarded-User` pattern in the dashboard suggests an upstream proxy is assumed to handle auth, but that assumption is undocumented.

**Why it matters.** If the orchestrator is reachable outside a trusted network, unauthenticated job submission is a cost/resource abuse vector and worker-endpoint enumeration aids targeting. Low severity: likely intentional for an internal service behind a proxy, but the assumption is unstated and easy to violate.

**Recommendation.** Document the trusted-network/proxy assumption explicitly, or add an optional auth dependency (e.g. the same token or an API key) to the mutating routes gated by an env var so prod can enforce it without changing the dev default.

**Verification.** Confirm the documented deployment model states the auth boundary; if an auth dependency is added, assert unauthenticated `POST /jobs` is rejected when the token is set.

### SEC-006 — Raw exception strings exposed in PlanResult.errors via OBS-004 fix

```yaml
status: open
severity: low
effort: S
reviewed_at: be7b3ab
last_verified_at:
  commit: 63faed4
  date: 2026-06-21
fixed_in: []
files:
  - path: src/acheron/shell/orchestrator.py
    lines: 319-345
related: [OBS-004, SEC-010]
```

**Issue.** The OBS-004 fix persists raw `str(exc)` from the top-level `except Exception` handler directly into `tracked.result.errors`. Unexpected exceptions may contain worker endpoints, file paths, library internals, or other implementation details that are now returned by `GET /jobs/{id}`.

**Why it matters.** Before the fix these details only lived in server logs; now they are exposed in API responses to anyone who can query job status, broadening the information-disclosure surface for the unauthenticated job routes noted in SEC-005.

**Recommendation.** Keep `logger.exception` for the full traceback, but populate `PlanResult.errors` with a sanitized or categorized message. For generic `Exception`, return a generic failure message and put the original exception detail in logs only.

**Verification.** Submit a job that fails with an exception whose message contains an internal path or endpoint; assert `GET /jobs/{id}` returns an error that does not contain that internal detail.

### SEC-007 — Host Path Traversal & Arbitrary Local File Read in ExtractionHandler

```yaml
status: open
severity: high
effort: M
reviewed_at: d9dc740
last_verified_at:
  commit: 63faed4
  date: 2026-06-21
fixed_in: []
files:
  - path: src/acheron/shell/local_handlers.py
    lines: 242-265
related: []
```

**Issue.** `ExtractionHandler` (local_handlers.py:242-265) accepts the `source_path` parameter directly from the user-controlled `job.payload` and reads or copies it without verifying that it lies within a permitted/sandboxed directory. For non-EPUB formats, it calls `_copy_audio` which copies the file directly into the step cache folder.

**Why it matters.** This allows any client to read arbitrary files accessible to the orchestrator process (e.g., `/etc/passwd`, private keys, env configurations) by submitting a request with `source_path` pointing to a sensitive file, posing a critical security risk.

**Recommendation.** Validate that `source_path` resolves to a path within a designated safe directory (e.g. using `Path.resolve()`), or implement strict sandboxing.

**Verification.** Submit an audio job with a path pointing to a sensitive file outside the workspace and verify it is rejected.

### SEC-008 — Auto-generated registration token is logged in plaintext at startup

```yaml
status: open
severity: critical
effort: S
reviewed_at: 63faed4
last_verified_at:
  commit: 63faed4
  date: 2026-06-21
fixed_in: []
files:
  - path: src/acheron/shell/orchestrator.py
    lines: 192
related: [SEC-002, MAINT-006]
```

**Issue.** `Orchestrator.start()` generates a 32-char token via `secrets.token_hex(16)` (orchestrator.py:188) and immediately emits it to the logger at INFO level: `logger.info("Generated and persisted registration token: %s", token)` (orchestrator.py:192). Any operator, log shipper, or downstream aggregation system (Datadog, Loki, journald, etc.) that ingests orchestrator logs now holds a valid bearer token for the worker-registration route. Because the verification path in `verify_registration_token` (deps.py:48) accepts any client that presents this token, an attacker with log access can register an arbitrary `endpoint` and receive job payloads (EPUB chapters, audio, intermediate artifacts) — the same exposure profile that SEC-002 mitigates is now undermined by the auto-generation feature designed to replace the default token.

**Why it matters.** The token is the sole authentication on `POST /workers` and gates who can advertise themselves as a worker. Logging it converts a well-scoped secret into a globally-readable one. A single log dump, a misconfigured log retention policy, or a leaked log-storage credential is sufficient to register a malicious worker and exfiltrate job data. The fix to SEC-002 (fail-closed on missing token) is partially undone because prod deployments that relied on the auto-generated token now leak it on every restart.

**Recommendation.** Remove the token value from the log message: `logger.info("Generated and persisted registration token to %s", token_file)`. If a human needs to copy the token for worker setup, log a one-time hint such as `logger.info("Generated registration token; see %s", token_file)` and rely on the persisted file (which is also the documented retrieval path). Also confirm the token file mode is 0600 (see SEC-009).

**Verification.** Start a fresh orchestrator with `ACHERON_DATA_DIR=$(mktemp -d)`, grep the resulting log for the generated token — it must not appear. Add a test that boots an orchestrator in a tmp dir and asserts the INFO log line does not contain the token. Additionally assert `stat -c '%a' $DATA_DIR/.registration_token` returns 600.

### SEC-009 — Registration token file created with process umask (potentially world-readable)

```yaml
status: open
severity: high
effort: S
reviewed_at: 63faed4
last_verified_at:
  commit: 63faed4
  date: 2026-06-21
fixed_in: []
files:
  - path: src/acheron/shell/orchestrator.py
    lines: 178-194
related: [SEC-001, SEC-008]
```

**Issue.** `Orchestrator.start()` writes the generated (or re-loaded) registration token via `token_file.write_text(token, encoding="utf-8")` (orchestrator.py:191) without setting a restrictive mode. The token file is then re-loaded from disk on every subsequent start (orchestrator.py:179-185) and grants bearer access to the worker-registration route. On a default umask of 022 the file is created world-readable (0o644); even on a typical 002 umask the file is group-readable. This is the same anti-pattern that SEC-001 flagged for CA private keys and is explicitly contradicted by the `OSError` write test fixture used in `tests/shell/test_orchestrator.py:419-439`, which never asserts file mode.

**Why it matters.** Token reads are a one-time, local-credential path: any local user (or any process running as a different user) on the orchestrator host can register workers. In container deployments with shared PID namespaces, or in CI/dev environments where the data dir is bind-mounted, the blast radius extends beyond a single user. This complements SEC-008: even if the log exposure is fixed, the on-disk file is still a high-value secret without explicit protection.

**Recommendation.** After `token_file.write_text(...)`, call `os.chmod(token_file, 0o600)` (or open the file with `opener` set to enforce mode 0o600 atomically). Apply the same fix to the persist path on line 191 and to any future re-write path. Update the orchestrator tests to assert `token_file.stat().st_mode & 0o777 == 0o600`.

**Verification.** Run the orchestrator with a fresh data dir and assert `stat -c '%a' $DATA_DIR/.registration_token` returns 600. Add a regression test in `tests/shell/test_orchestrator.py::test_orchestrator_generates_and_persists_registration_token` that introspects the file mode and fails on 0o644 / 0o640.

### SEC-010 — Worker `last_error` exposed via unauthenticated /workers endpoint (info disclosure)

```yaml
status: open
severity: low
effort: S
reviewed_at: 63faed4
last_verified_at:
  commit: 63faed4
  date: 2026-06-21
fixed_in: []
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

