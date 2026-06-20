---
branch: chore/code-review-update
initial_review_commit: 23c29e1
last_updated_commit: d0b739b
last_staleness_scan:
  commit: d0b739b
  date: 2026-06-20
---

# Operations

## PERF — Performance

**Grade:** A

PERF-001 is fixed (concurrent health probes at 0818bff). Two open medium findings: registry list_all() is still called per step in the dispatch hot path, and worker transport instances are reconstructed per step with no HTTP connection or gRPC channel reuse.

### PERF-001 — Health checks run sequentially, blocking the whole sweep on slow/dead workers

```yaml
status: fixed
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
status: open
severity: medium
effort: M
reviewed_at: 23c29e1
last_verified_at:
  commit: d0b739b
  date: 2026-06-20
fixed_in: []
files:
  - path: src/acheron/shell/step_handler.py
    lines: 84-114
  - path: src/acheron/shell/orchestrator.py
    lines: 143-188
related: []
```

**Issue.** The step handler (step_handler.py:88) calls `await registry.list_all()` on EVERY step dispatch to find a matching worker, and orchestrator.submit_job calls list_all() again to compile the plan. For a plan with S steps this is S+1 list_all() calls; the registry is read-only during a single job's execution. For the Redis backend (stores/redis.py:308-317) each list_all() does `smembers` plus a pipelined `hgetall` per worker, so with W workers the dispatch hot path makes (S+1)*(1+W) Redis round-trips purely for worker discovery that could be satisfied by one snapshot.

**Why it matters.** Each redundant list_all() adds a Redis pipeline round-trip to every step; for a 5-step plan against 10 workers that's ~55 round-trips where 1 suffices, lengthening end-to-end latency and increasing Redis load under concurrent jobs. Medium severity: measurable latency on the main execution path, but not a correctness risk.

**Recommendation.** Snapshot the worker list once per plan execution and pass it into the handler (or cache list_all() on the store with invalidation on register/unregister). The executor could fetch workers once and thread the selected worker through, or the handler could memoize list_all() per plan_id.

**Verification.** Add a test asserting list_all() is called once per plan (not once per step) during execute(); instrument Redis and observe round-trip count drop from (S+1)*(1+W) to ~1+W.

### PERF-003 — Worker transport instances reconstructed per step (no HTTP connection or gRPC channel reuse)

```yaml
status: open
severity: medium
effort: M
reviewed_at: 23c29e1
last_verified_at:
  commit: d0b739b
  date: 2026-06-20
fixed_in: []
files:
  - path: src/acheron/shell/step_handler.py
    lines: 111-113
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

## OBS — Observability

**Grade:** A

### OBS-001 — Shutdown does not drain in-flight _execute tasks; cancelled jobs stay stuck at "running"

```yaml
status: open
severity: medium
effort: M
reviewed_at: 23c29e1
last_verified_at:
  commit: d0b739b
  date: 2026-06-20
fixed_in: []
files:
  - path: src/acheron/shell/orchestrator.py
    lines: 133-141
  - path: src/acheron/shell/orchestrator.py
    lines: 184-186
  - path: src/acheron/shell/orchestrator.py
    lines: 190-219
related: [OBS-004]
```

**Issue.** `Orchestrator.shutdown()` (orchestrator.py:133-141) stops only the health monitor; it never cancels or awaits the `_execute` tasks tracked in `self._tasks` (populated at submit_job:184-186). The FastAPI lifespan (api/app.py:30-32) then calls `close()` which tears down the Redis pool. When the loop tears down, in-flight `_execute` tasks are cancelled mid-run; `CancelledError` is a `BaseException` so the `except AcheronError`/`except Exception` guards at lines 213,216 don't catch it, and the final `await self._job_store.put(tracked)` at line 219 sits outside any `finally`, so it is skipped. The job is left persisted with status="running" and never updated. The PlanStatus enum fix (TYPE-002) changed `status = "failed"` to `status = PlanStatus.FAILED` but did not address the drain gap.

**Why it matters.** After any orchestrator restart, previously in-flight jobs are permanently stuck at "running" in the job store with nothing executing them; operators and the dashboard cannot distinguish truly-running from orphaned jobs. Medium severity: silent persisted-state corruption that misleads observability and can block cleanup/retry logic.

**Recommendation.** In `shutdown()`, cancel and await `_tasks` with a grace timeout, and move the `job_store.put(tracked)` in `_execute` into a `finally` block (setting status="failed" on `CancelledError`) so the persisted state always reflects reality. Alternatively, on `start()`, mark any persisted "running" jobs as "failed" (interrupted).

**Verification.** Start a job, call `shutdown()` mid-execution, then inspect the job store: the job should be "failed" (or otherwise reconciled), not "running". Add a test that cancels `_execute` and asserts `job_store.put` ran with a terminal status.

### OBS-002 — Dashboard silently swallows orchestrator connection errors

```yaml
status: open
severity: low
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: d0b739b
  date: 2026-06-20
fixed_in: []
files:
  - path: dashboard/app.py
    lines: 27-34
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
  commit: d0b739b
  date: 2026-06-20
fixed_in: []
files:
  - path: src/acheron/shell/orchestrator.py
    lines: 161-173
  - path: src/acheron/shell/health.py
    lines: 89-112
  - path: src/acheron/shell/step_handler.py
    lines: 111
related: []
```

**Issue.** All logging uses free-form `%s` format strings (e.g. orchestrator.py:224 "Submitting job %s: %s → %s (%s, %s)"). There is no structured/JSON logging and no correlation token beyond job_id appearing inside message text. In a distributed system with concurrent jobs, workers, and health checks, correlating a failure across orchestrator→transport→worker requires grepping free-text rather than filtering on fields.

**Why it matters.** Free-form logs are harder to query and aggregate in prod log systems and lack stable field names for job_id/worker_id/step_id/trace_id, weakening cross-component traceability. Low severity: a consistency/observability gap, not a functional failure.

**Recommendation.** Adopt structured logging (e.g. structlog or stdlib `extra=` with a JSON formatter) with stable fields (job_id, worker_id, step_id, strategy) so failures trace across orchestrator→transport→worker by field rather than text.

**Verification.** Run a job end-to-end and confirm log entries carry job_id/step_id as queryable fields in the emitted JSON.

### OBS-004 — Top-level execution failures set status but persist no error detail

```yaml
status: open
severity: low
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: d0b739b
  date: 2026-06-20
fixed_in: []
files:
  - path: src/acheron/shell/orchestrator.py
    lines: 213-219
related: [CORR-004, OBS-001]
```

**Issue.** When `executor.run()` raises `AcheronError` or `Exception`, `_execute` (orchestrator.py:213-219) sets `tracked.status = PlanStatus.FAILED` but never populates `tracked.result` (it stays None), then persists the job at line 219. The API's `_tracked_to_response` (jobs.py:72-83) returns `errors=[]` when result is None, so a consumer of `GET /jobs/{id}` sees `status="failed"` with no error detail — the failure reason exists only in server logs. The PlanStatus enum fix changed the status assignment but did not address the missing error detail.

**Why it matters.** Operators diagnosing failures via the API see a failed job with an empty error list, forcing them to correlate logs by job_id/time. Low severity: the status is correct and the detail is in logs, but the API is misleading for triage.

**Recommendation.** When `_execute` catches a top-level exception, synthesize a minimal PlanResult (or extend TrackedJob with an error field) so the failure reason is persisted alongside the status.

**Verification.** Trigger a worker failure that propagates out of `executor.run()`; `GET /jobs/{id}` and assert `errors` is non-empty and names the failure.

## SEC — Security

**Grade:** A

SEC-001 through SEC-003 are now fixed. SEC-004 (dashboard X-Forwarded-User spoofing, low) and SEC-005 (unauthenticated routes, low) remain open. No secrets are logged; Jinja2 autoescape is on; Redis uses `json.loads` (not pickle); path traversal is not exploitable (server-generated UUIDs).

### SEC-001 — Dev cert private keys written world-readable (mode 0644)

```yaml
status: fixed
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
status: fixed
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
status: fixed
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
status: open
severity: low
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: d0b739b
  date: 2026-06-20
fixed_in: []
files:
  - path: dashboard/app.py
    lines: 36-39
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
