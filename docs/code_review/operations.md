---
branch: docs/code-review-initial
initial_review_commit: 23c29e1
last_updated_commit: 23c29e1
last_staleness_scan:
  commit: 23c29e1
  date: 2026-06-19
---

# Operations

## PERF — Performance

**Grade:** B

Three medium findings on the dispatch hot path: `registry.list_all()` is called per step (N+1 Redis round-trips per plan), health checks run sequentially (blocking the sweep on dead workers), and worker transport instances (HTTP connections, gRPC channels) are reconstructed per step with no reuse. Redis deserialization uses `json.loads` throughout (not pickle) — safe, no SEC finding. Python 3.14's unparenthesized `except A, B:` syntax is used correctly in several files (verified) — not a finding.

### PERF-001 — Health checks run sequentially, blocking the whole sweep on slow/dead workers

```yaml
status: open
severity: medium
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: pending
  date: 2026-06-19
fixed_in: []
files:
  - path: src/acheron/shell/health.py
    lines: 89-102
related: [REPRO-002]
```

**Issue.** `HealthMonitor._check_all` (health.py:96-101) iterates registered workers and `await`s `self._health_check(...)` plus the `record_health_*` calls one worker at a time. Each HTTP/gRPC probe has a 5s timeout (health.py:29, gRPC default). With W workers and K unreachable ones, a single sweep takes up to K*5s + (W-K)*t before the next worker is even probed, and the 30s interval (health.py:61) can slip or overlap.

**Why it matters.** A few dead workers serialize the entire health sweep, delaying removal of other unhealthy workers and delaying detection of newly-healthy ones; with many workers the monitor effectively stops keeping up. Medium severity: degrades the reliability of the failure-detection loop under the exact condition (dead workers) it exists to handle.

**Recommendation.** Probe all workers concurrently with `asyncio.gather(*(self._health_check(w.endpoint, w.transport) for w in workers), return_exceptions=True)`, then process the results and fire the `record_health_*` calls (optionally also gathered).

**Verification.** Test with N fake workers where M sleep 5s before responding; assert `_check_all` completes in ~5s (one timeout window) rather than ~M*5s.

### PERF-002 — Registry list_all() called per step in dispatch hot path (N+1 round-trips)

```yaml
status: open
severity: medium
effort: M
reviewed_at: 23c29e1
last_verified_at:
  commit: pending
  date: 2026-06-19
fixed_in: []
files:
  - path: src/acheron/shell/step_handler.py
    lines: 84-113
  - path: src/acheron/shell/orchestrator.py
    lines: 233-266
related: []
```

**Issue.** The step handler (step_handler.py:88) calls `await registry.list_all()` on EVERY step dispatch to find a matching worker, and orchestrator.submit_job (orchestrator.py:233) calls list_all() again to compile the plan. For a plan with S steps this is S+1 list_all() calls; the registry is read-only during a single job's execution. For the Redis backend (stores/redis.py:308-317) each list_all() does `smembers` plus a pipelined `hgetall` per worker, so with W workers the dispatch hot path makes (S+1)*(1+W) Redis round-trips purely for worker discovery that could be satisfied by one snapshot.

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
  commit: pending
  date: 2026-06-19
fixed_in: []
files:
  - path: src/acheron/shell/step_handler.py
    lines: 111-113
  - path: src/acheron/shell/transports/http.py
    lines: 41-49
  - path: src/acheron/shell/transports/grpc.py
    lines: 34-41
related: []
```

**Issue.** step_handler.py:112 builds a fresh `worker_instance = factory(selected)` on every step call. For HTTP this constructs a new HttpWorker that, lacking a shared client, opens a throwaway `httpx.AsyncClient` per request (http.py:48) — no keep-alive/connection reuse. For gRPC, `default_worker_factory` (step_handler.py:40) calls `grpc_channel(endpoint)` creating a new `grpc.aio.Channel` per step; channels are expensive to establish (TCP+TLS handshake). Across a multi-step plan and concurrent jobs this multiplies connection setup cost.

**Why it matters.** Per-step connection churn adds latency and file-descriptor pressure on the orchestrator and worker sides, and defeats HTTP keep-alive / gRPC channel multiplexing that exists precisely for repeated calls to the same endpoint. Medium severity: latency and resource overhead on the dispatch hot path, scaling with job concurrency.

**Recommendation.** Cache worker instances (or at least the underlying `httpx.AsyncClient` / `grpc.aio.Channel`) per worker_id/endpoint on the step handler or a small worker pool, reusing them across steps and jobs; close them on orchestrator shutdown.

**Verification.** Instrument channel/client construction; assert one Channel/AsyncClient per distinct worker endpoint across a multi-step plan rather than one per step.

## OBS — Observability

**Grade:** A

One medium finding: shutdown does not drain in-flight `_execute` tasks, leaving cancelled jobs permanently stuck at "running" in the job store. Three low findings cover top-level execution failures persisting no error detail, free-form logs with no structured fields or trace correlation, and the dashboard silently swallowing orchestrator connection errors. Logging is consistent (stdlib `logging` everywhere in shell/core; `rich` console only for CLI user output) — no mixed print/logging finding.

### OBS-001 — Shutdown does not drain in-flight _execute tasks; cancelled jobs stay stuck at "running"

```yaml
status: open
severity: medium
effort: M
reviewed_at: 23c29e1
last_verified_at:
  commit: pending
  date: 2026-06-19
fixed_in: []
files:
  - path: src/acheron/shell/orchestrator.py
    lines: 196-204
  - path: src/acheron/shell/orchestrator.py
    lines: 247-282
related: [OBS-004]
```

**Issue.** `Orchestrator.shutdown()` (orchestrator.py:196-204) stops only the health monitor; it never cancels or awaits the `_execute` tasks tracked in `self._tasks` (populated at submit_job:248-249). The FastAPI lifespan (api/app.py:30-32) then calls `close()` which tears down the Redis pool. When the loop tears down, in-flight `_execute` tasks are cancelled mid-run; `CancelledError` is a `BaseException` so the `except AcheronError`/`except Exception` guards at lines 276-281 don't catch it, and the final `await self._job_store.put(tracked)` at line 282 sits outside any `finally`, so it is skipped. The job is left persisted with status="running" and never updated.

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
  commit: pending
  date: 2026-06-19
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
  commit: pending
  date: 2026-06-19
fixed_in: []
files:
  - path: src/acheron/shell/orchestrator.py
    lines: 224-236
  - path: src/acheron/shell/health.py
    lines: 96-102
  - path: src/acheron/shell/step_handler.py
    lines: 111-113
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
  commit: pending
  date: 2026-06-19
fixed_in: []
files:
  - path: src/acheron/shell/orchestrator.py
    lines: 253-282
related: [CORR-004, OBS-001]
```

**Issue.** When `executor.run()` raises `AcheronError` or `Exception`, `_execute` (orchestrator.py:276-281) sets `tracked.status = "failed"` but never populates `tracked.result` (it stays None), then persists the job at line 282. The API's `_tracked_to_response` (jobs.py:72-83) returns `errors=[]` when result is None, so a consumer of `GET /jobs/{id}` sees `status="failed"` with no error detail — the failure reason exists only in server logs.

**Why it matters.** Operators diagnosing failures via the API see a failed job with an empty error list, forcing them to correlate logs by job_id/time. Low severity: the status is correct and the detail is in logs, but the API is misleading for triage.

**Recommendation.** When `_execute` catches a top-level exception, synthesize a minimal PlanResult (or extend TrackedJob with an error field) so the failure reason is persisted alongside the status.

**Verification.** Trigger a worker failure that propagates out of `executor.run()`; `GET /jobs/{id}` and assert `errors` is non-empty and names the failure.

## SEC — Security

**Grade:** B

Three medium findings: dev cert private keys are written world-readable (mode 0644, including the CA key), worker registration fails open when `ACHERON_REGISTRATION_TOKEN` is unset (silent data exposure to attacker-controlled worker endpoints), and TLS is silently disabled when CA env vars are unset (plaintext gRPC/HTTP with no warning). Two low findings cover the dashboard trusting a spoofable `X-Forwarded-User` header and job/capabilities routes requiring no authentication. No secrets are logged; Jinja2 autoescape is on (no XSS); Redis uses `json.loads` (not pickle, no deserialization risk); path traversal is not exploitable (server-generated UUIDs for job/plan IDs).

### SEC-001 — Dev cert private keys written world-readable (mode 0644)

```yaml
status: open
severity: medium
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: pending
  date: 2026-06-19
fixed_in: []
files:
  - path: scripts/generate_dev_certs.py
    lines: 38-49
related: []
```

**Issue.** `_write_pem_key` (generate_dev_certs.py:38-49) chmods every private key — including the CA key (line 96) and all service keys — to `0o644` (world-readable). The comment (lines 46-48) acknowledges this is intentional so a "future non-root user" can read the cert, but that rationale applies to certificates, not private keys; the CA private key at 0644 lets any local user sign certificates trusted by the dev CA.

**Why it matters.** World-readable private keys violate the general security rule that key material must be 0600; the CA key exposure in particular enables rogue cert signing. Even in dev this establishes a bad pattern that can leak if certs are copied into a shared image or host. Medium severity: dev-only blast radius today, but the CA key signing capability is a real local privilege risk.

**Recommendation.** Write private keys with mode `0o600` (owner read/write only); keep certificates at `0o644`. If a non-root worker must read keys, use group ownership and `0o640` instead of world-readable.

**Verification.** Run `just certs` and `stat -c '%a' certs/*.key`; assert keys are 0600 (or 0640) and certs are 0644.

### SEC-002 — Worker registration fails open when ACHERON_REGISTRATION_TOKEN is unset

```yaml
status: open
severity: medium
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: pending
  date: 2026-06-19
fixed_in: []
files:
  - path: src/acheron/shell/api/deps.py
    lines: 22-31
related: []
```

**Issue.** `verify_registration_token` (deps.py:24-26) returns immediately with no error when `ACHERON_REGISTRATION_TOKEN` is unset, allowing open registration. `.env.example` documents this as dev-only, but there is no startup warning and no fail-closed mode, so a prod deployment that forgets the env var silently accepts worker registrations from any client — including a malicious worker whose endpoint points at an attacker server receiving job payloads (source EPUBs/audio).

**Why it matters.** Fail-open auth on worker registration can leak job data to attacker-controlled endpoints with no log signal. Medium severity: requires a misconfiguration but produces silent data exposure in prod.

**Recommendation.** Log a WARNING at startup when the token is unset, and/or add an explicit `ACHERON_OPEN_REGISTRATION=1` opt-in so open registration is an intentional flag rather than the default-of-absence. Use `secrets.compare_digest` (already done) for the comparison when a token is set.

**Verification.** Start the API with no token set and assert a visible startup warning; assert registration is rejected (or explicitly opted-in) rather than silently allowed.

### SEC-003 — TLS silently disabled when CA env vars are unset (gRPC insecure_channel / uvicorn plain HTTP)

```yaml
status: open
severity: medium
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: pending
  date: 2026-06-19
fixed_in: []
files:
  - path: src/acheron/shell/tls.py
    lines: 52-75
  - path: src/acheron/shell/api/__main__.py
    lines: 19-26
related: [CFG-002]
```

**Issue.** `grpc_channel_credentials` (tls.py:60) returns `None` when neither `ACHERON_TLS_CA_FILE` nor `SSL_CERT_FILE` is set, and `grpc_channel` (tls.py:74-75) then returns `grpc.aio.insecure_channel` — so all orchestrator→worker gRPC traffic (job payloads with source text/audio) goes plaintext with no warning. Likewise `uvicorn_ssl_kwargs` (tls.py:34-37) returns `{}` when cert/key are unset, so `api/__main__.py` serves plain HTTP. The CLI defaults to HTTPS (cli.py:36) but the server defaults to no TLS, an asymmetric and easy-to-misconfigure default.

**Why it matters.** A prod deployment missing the CA env var gets plaintext transport of potentially sensitive job content with no log signal that encryption is off. Medium severity: silent transport-security downgrade on misconfiguration.

**Recommendation.** Log a WARNING at startup when TLS is not configured on the server or when the gRPC channel falls back to insecure; consider requiring an explicit `ACHERON_ALLOW_INSECURE=1` for non-TLS prod operation, or defaulting the server to TLS like the CLI does.

**Verification.** Start the API and a gRPC worker with no TLS env vars; assert a visible startup warning naming the plaintext fallback, and assert `grpc_channel` only returns `insecure_channel` under explicit opt-in.

### SEC-004 — Dashboard trusts spoofable X-Forwarded-User header as identity

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
  - path: dashboard/app.py
    lines: 37-39
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
  commit: pending
  date: 2026-06-19
fixed_in: []
files:
  - path: src/acheron/shell/api/routes/jobs.py
    lines: 20-69
  - path: src/acheron/shell/api/routes/capabilities.py
    lines: 13-22
related: []
```

**Issue.** Only the worker-registration route injects `RegistrationTokenDep` (workers.py:22). Job submission (jobs.py:21), job listing (jobs.py:66), get_job, and capabilities (capabilities.py:14) have no auth dependency at all, so any network-reachable client can submit jobs (consuming worker resources/cost) and enumerate worker endpoints. The `X-Forwarded-User` pattern in the dashboard suggests an upstream proxy is assumed to handle auth, but that assumption is undocumented.

**Why it matters.** If the orchestrator is reachable outside a trusted network, unauthenticated job submission is a cost/resource abuse vector and worker-endpoint enumeration aids targeting. Low severity: likely intentional for an internal service behind a proxy, but the assumption is unstated and easy to violate.

**Recommendation.** Document the trusted-network/proxy assumption explicitly, or add an optional auth dependency (e.g. the same token or an API key) to the mutating routes gated by an env var so prod can enforce it without changing the dev default.

**Verification.** Confirm the documented deployment model states the auth boundary; if an auth dependency is added, assert unauthenticated `POST /jobs` is rejected when the token is set.
