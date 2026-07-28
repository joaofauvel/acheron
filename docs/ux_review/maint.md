---
theme: MAINT
last_updated_date: 2026-07-28
version: 2
---

# MAINT

**Grade**: C (12 high + 4 medium-severity open stories)
**Calibration target**: an on-call engineer should be able to recover from a 2am page without paging someone else.

## MAINT-001 — No admin endpoints to reap stuck `RUNNING` jobs

```yaml
---
id: MAINT-001
title: "`shell/api/routes/` has no admin namespace; on-call cannot reap / mark-failed / drain stuck jobs after orchestrator restart"
status: open
severity: high
effort: M
discovered_via: [on-call, code-review]
user_facing_surface: cli
silent: true
journey_stage: t2
user_journey: "On-call engineer SSHes in after a 2am page: orchestrator was `kill -9` during a deploy, restarted, finds 7 jobs in `status=RUNNING` with no active task. Engineer runs `acheron admin reap-stuck --older-than 60s --reason orphaned_by_restart`, sees 7 jobs reaped, each marked FAILED with the reason; dashboard shows the per-job FAILED rows within 5s."
files:
  - path: src/acheron/shell/orchestrator.py
    lines: 274-340
  - path: src/acheron/shell/api/routes/jobs.py
    lines: 1-108
related: [OBS-001, OBS-014, OBS-015]
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
incident_ref: TBD-pagerduty
---
```

**Issue.** The orchestrator's recovery paths are in-process. A `kill -9` skips the lifespan. In-flight jobs stay in `RUNNING` state in Redis forever. The shell API at `routes/jobs.py` has only `POST /jobs`, `GET /jobs`, `POST /jobs/{id}/resume` — no DELETE / `POST /jobs/{id}/cancel` / `POST /admin/*`. The on-call's only path is `redis-cli hset job:<id> status failed`.

**Why it matters.** 5 of 14 on-call failure modes require this recovery.

**Recommendation.** Add a `/admin` namespace: `POST /admin/jobs/{id}/mark-failed`, `POST /admin/jobs/reap-stale?older-than=60s`, `POST /admin/drain`, `POST /admin/rotate-token`. Plumb CLI: `acheron admin reap-stuck --older-than 60s --reason ...`.

**Verification.** With 3 stuck jobs, `acheron admin reap-stuck --older-than 60s` returns 200 with `{ reaped: 3, job_ids: [...] }`.

## MAINT-002 — Failed job cost row doesn't show GPU / cache age

```yaml
---
id: MAINT-002
title: "When a job is FAILED with `gpu_seconds` recorded, the cost row doesn't show which GPU the rate was queried for, or how stale it is"
status: open
severity: high
effort: S
discovered_via: [on-call, code-review]
user_facing_surface: dashboard
silent: true
journey_stage: t2
user_journey: "On-call sees a FAILED job with `gpu_seconds=1800, cost=$0.34`, clicks the cost row, sees a popover: 'L4 community cloud, measured 4m ago at 9:55am; rate: $0.69/hr; cache age: 4m 22s.'"
files:
  - path: src/acheron/worker_sdk/pricing.py
    lines: 248-263
  - path: src/acheron/core/models.py
    lines: 68-74
related: [CORR-008, CORR-040, TYPE-005]
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
incident_ref: TBD-pagerduty
---
```

**Issue.** The cost basis enum is rendered in the dashboard, but the underlying data (GPU type, query timestamp, cache age) is not surfaced. The on-call investigating a failed job with `cost=$0.34` cannot tell whether the rate is for an L4 or an H100, cannot tell whether the cache was 4 minutes stale or 4 hours stale.

**Why it matters.** Cost forensics is part of the on-call's job.

**Recommendation.** Extend `PriceEstimate` with `gpu_type: str | None`, `queried_at: datetime | None`, `cache_age_seconds_at_estimate: float | None`. Surface in `JobResponse` (or a `cost_breakdown` sub-object).

**Verification.** A job with `MEASURED` basis shows the GPU type, query timestamp, and cache age in both the dashboard popover and `acheron job cost --explain`.

## MAINT-003 — Cert expiry is silent — no warning at 30/7/0 days remaining

```yaml
---
id: MAINT-003
title: "Cert expiry is silent — orchestrator reads `ACHERON_TLS_CERT_FILE` once and emits no warning at 30/7/0 day marks"
status: open
severity: high
effort: M
discovered_via: [on-call, code-review, audit]
user_facing_surface: certs
silent: true
journey_stage: t2
user_journey: "On-call at 2am is paged because all worker → orchestrator gRPC calls fail. Engineer runs `acheron certs status` and sees `orchestrator.crt expires in 0d 0h 14m`, plus warnings emitted to the orchestrator's log at the 30/7/0 day marks; rotates the cert via `acheron certs renew`, workers reconnect within 5s."
files:
  - path: src/acheron/tls.py
    lines: 36-52
  - path: scripts/generate_dev_certs.py
    lines: 21-31
related: []
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
incident_ref: TBD-pagerduty
---
```

**Issue.** The orchestrator's TLS layer reads `ACHERON_TLS_CERT_FILE` once at process start and never re-reads it. There is no expiry check, no `not_after` introspection, no warning emitted at the 30/7/0-day marks, and no `/admin/certs/status` endpoint. The CA + service certs minted by `generate_dev_certs.py:21-31` are `VALIDITY_DAYS = 365` and there is no cron / healthcheck / lifespan hook that re-checks them.

**Why it matters.** Cert expiry is the #1 silent failure: the workers can't connect, the orchestrator's `/health` still returns 200, the dashboard still loads.

**Recommendation.** (a) On orchestrator startup, parse each cert and log a single INFO line per cert with `expires_at`, `days_remaining`, and `subject`. (b) Schedule a daily background task that emits WARNING at 30/7 days, ERROR at 1 day, and CRITICAL at 0. (c) Add `acheron certs status` (CLI) and `GET /admin/certs/status` (HTTP).

**Verification.** After standing up the orchestrator with a 31-day cert, the startup log shows `INFO cert=orchestrator.crt expires_at=... days_remaining=31`. Stub time forward to 29 days: the daily task emits `WARNING cert=orchestrator.crt days_remaining=29`.

## MAINT-004 — Dev cert SAN list breaks production TLS verify

```yaml
---
id: MAINT-004
title: "Dev cert SAN list is `localhost` / `127.0.0.1` only — production deploy with the orchestrator's real hostname fails TLS verify on every worker handshake"
status: open
severity: high
effort: M
discovered_via: [on-call, first-run, code-review]
user_facing_surface: certs
silent: true
journey_stage: t2
user_journey: "Deployer with the orchestrator on `orch.example.com` runs `just certs` (which calls `scripts/generate_dev_certs.py`), then `docker compose up`. Workers fail to connect with `certificate verify failed: Hostname mismatch`; `just certs --san orch.example.com` regenerates with the operator-supplied SAN list, workers connect."
files:
  - path: scripts/generate_dev_certs.py
    lines: 21-31
  - path: scripts/generate_dev_certs.py
    lines: 120-129
  - path: docker-compose.yml
    lines: 36-44
related: []
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
incident_ref: TBD-pagerduty
---
```

**Issue.** `scripts/generate_dev_certs.py:120-129` hard-codes the SAN list to `[service, "localhost", 127.0.0.1]` per-service. A real deploy with the orchestrator's DNS name fails with `ssl: hostname 'orch.example.com' doesn't match 'orchestrator'` on every worker gRPC call.

**Why it matters.** The dev cert generator is the only cert generator. There is no production-cert path documented.

**Recommendation.** `scripts/generate_dev_certs.py` accepts `--san` (repeatable) and `--service` (repeatable) CLI flags. The default for `--san` is `localhost,127.0.0.1`.

**Verification.** `just certs --san orch.example.com` regenerates `orchestrator.crt` with `DNS:orch.example.com`. The orchestrator running on `orch.example.com` accepts a gRPC connection from a worker on a different host.

## MAINT-005 — Cert rotation requires orchestrator restart

```yaml
---
id: MAINT-005
title: "Cert rotation requires orchestrator restart — `uvicorn` does not reload `ssl_certfile` / `ssl_keyfile` after a SIGHUP, and the operator has no `/admin/certs/reload` endpoint"
status: open
severity: medium
effort: M
discovered_via: [on-call, code-review]
user_facing_surface: certs
silent: true
journey_stage: t2
user_journey: "On-call rotates `orchestrator.crt` and `orchestrator.key` on disk after the 30-day warning fires (MAINT-003). Operator runs `acheron certs reload`; the orchestrator's `uvicorn` server reloads the cert/key without bouncing (no downtime); workers continue to connect."
files:
  - path: src/acheron/tls.py
    lines: 36-52
  - path: src/acheron/shell/orchestrator.py
    lines: 233-253
related: [MAINT-003]
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
incident_ref: TBD-pagerduty
---
```

**Issue.** `uvicorn_ssl_kwargs` is consumed once at process start. uvicorn's `ssl_certfile` / `ssl_keyfile` are not hot-reloadable. There is no `/admin/certs/reload` HTTP endpoint, no `acheron certs reload` CLI command, no signal handler. The on-call's only path is `docker compose restart orchestrator` (5-15s of downtime).

**Why it matters.** Cert rotation is a routine on-call task. A bounce is acceptable during a planned maintenance window but not during a 2am expiry page.

**Recommendation.** Add `GET /admin/certs/status` and `POST /admin/certs/reload` to the shell API.

**Verification.** With a 30-day cert, rotate to a 365-day cert on disk. `acheron certs reload` returns 200; the orchestrator log shows `INFO reloaded cert: ...`; `openssl s_client -connect orchestrator:8000` returns the new cert's `not_after`.

## MAINT-006 — Registration-token auto-mint is unreachable in compose

```yaml
---
id: MAINT-006
title: "Registration-token auto-mint is unreachable in compose — `ACHERON_REGISTRATION_TOKEN` is `required` in `docker-compose.yml:36`, so the auto-mint path at `orchestrator.py:273-282` is dead code in the supported deploy"
status: open
severity: medium
effort: S
discovered_via: [on-call, first-run, code-review]
user_facing_surface: compose
silent: true
journey_stage: t2
user_journey: "Deployer on a fresh checkout follows the README and runs `docker compose up`. Compose refuses to start: `ACHERON_REGISTRATION_TOKEN must be set`. Deployer must run `openssl rand -hex 32` manually, set the env var in `.env`, and re-run `docker compose up`. The orchestrator's documented 'auto-generates and persists' path is never reached because compose requires the env var up front."
files:
  - path: docker-compose.yml
    lines: 33-44
  - path: src/acheron/shell/orchestrator.py
    lines: 255-282
related: []
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
incident_ref: TBD-pagerduty
---
```

**Issue.** `docker-compose.yml:36` declares `ACHERON_REGISTRATION_TOKEN: ${ACHERON_REGISTRATION_TOKEN:?ACHERON_REGISTRATION_TOKEN must be set}` — the `?:` form aborts compose startup if the env var is unset. `_load_or_create_registration_token` is structured to auto-mint when the env var is unset, but in the supported deploy the env var is *required* by compose, so the mint branch is unreachable.

**Why it matters.** The auto-mint path is the documented on-ramp for a fresh deploy.

**Recommendation.** Change `docker-compose.yml:36` from `${VAR:?...}` to `${VAR:-}` so compose starts with the env var unset, and the orchestrator's auto-mint path fires. Persist the minted token; the workers read the same path via a shared volume.

**Verification.** Fresh `docker compose up` with `.env` unset starts cleanly. `cat certs/.registration_token` shows a 32-char hex string. The workers register successfully.

## MAINT-007 — No `acheron token rotate` command and no audit trail

```yaml
---
id: MAINT-007
title: "No `acheron token rotate` command and no audit trail — the on-disk `.registration_token` has no creation timestamp and no rotation history"
status: open
severity: high
effort: M
discovered_via: [on-call, code-review, audit]
user_facing_surface: cli
silent: true
journey_stage: t2
user_journey: "On-call at 2am runs `acheron token status` and sees `created_at=2024-01-15 rotations=0 current_token=ab12...`. Engineer runs `acheron token rotate --reason incident-2026-07-24-worker-401`; a new token is generated, persisted to `.registration_token` (mode 0600), the previous token is appended to `.registration_token.history` with `rotated_at` and `reason`."
files:
  - path: src/acheron/shell/orchestrator.py
    lines: 255-282
  - path: src/acheron/worker_sdk/registration.py
    lines: 42-69
related: [MAINT-006, SEC-008]
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
incident_ref: TBD-pagerduty
---
```

**Issue.** The registration token is loaded with no creation timestamp, no rotation history, and no audit log. There is no `acheron token rotate` command, no `/admin/token/rotate` HTTP endpoint.

**Why it matters.** Token rotation is a security-incident response procedure.

**Recommendation.** Persist the token to `<data_dir>/.registration_token` with a header `created_at=ISO8601\ntoken=HEX\n`. On every rotate, append a JSONL line to `<data_dir>/.registration_token.history` with `{ts, old_token_sha256_prefix8, new_token_sha256_prefix8, reason}`. Add `POST /admin/token/rotate`.

**Verification.** `acheron token rotate --reason "test"` returns a new token; the history file has one JSONL line; `cat .registration_token` shows the new value.

## MAINT-008 — No "stuck > N minutes" filter in `list_jobs`

```yaml
---
id: MAINT-008
title: "No \"stuck > N minutes\" filter in `list_jobs` — the on-call at 2am cannot find the 3 stuck jobs among 200 in the dashboard's flat list"
status: open
severity: high
effort: S
discovered_via: [on-call, code-review]
user_facing_surface: dashboard
silent: true
journey_stage: t2
user_journey: "On-call at 2am opens the dashboard, sees 200 jobs (190 SUCCESS, 3 FAILED, 7 RUNNING). Engineer clicks the 'Stuck' filter, sets 'Older than 30 min', and the list collapses to the 3 RUNNING jobs whose `submitted_at` is > 30 min ago."
files:
  - path: src/acheron/shell/orchestrator.py
    lines: 735-737
  - path: src/acheron/shell/api/routes/jobs.py
    lines: 1-108
related: [MAINT-001]
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
incident_ref: TBD-pagerduty
---
```

**Issue.** `list_jobs` returns every job in the store with no filter. The shell API has only `POST /jobs`, `GET /jobs`, `POST /jobs/{id}/resume` — no `?status=RUNNING&older_than=30m`. The on-call at 2am has no way to ask "show me only the stuck ones."

**Why it matters.** The MAINT-001 reap-stuck admin endpoint is the recovery half. The on-call needs to *find* the stuck jobs first.

**Recommendation.** Add `GET /jobs?status=RUNNING&older_than=1800` to the shell API. The dashboard's jobs list view adds a "Stuck only" toggle and an "older than" numeric input.

**Verification.** Submit 5 jobs: 2 RUNNING, 1 FAILED, 2 SUCCESS. Wait 31 min. `GET /jobs?status=RUNNING&older_than=30m` returns the 2 RUNNING jobs only.

## MAINT-009 — BOOTING timeout is hard-coded to 600s with no operator visibility

```yaml
---
id: MAINT-009
title: BOOTING timeout is hard-coded to 600s with no operator visibility — workers silently flip BOOTING → OFFLINE with no countdown, warning, or log breadcrumb
status: fixed
severity: high
effort: S
discovered_via: [on-call, code-review]
user_facing_surface: dashboard
silent: true
journey_stage: t2
user_journey: "On-call sees a worker in `BOOTING` for 8 minutes; the dashboard's worker row shows `BOOTING — 434s / 600s` with a progress bar. At 9m 0s, the orchestrator logs WARNING. At 10m 0s, the row flips to `OFFLINE` with reason `provider BOOTING timeout exceeded`."
files:
  - path: src/acheron/shell/health.py
    lines: 27-29
  - path: src/acheron/shell/health.py
    lines: 174-226
  - path: src/acheron/shell/registry.py
    lines: 23-32
  - path: src/acheron/shell/stores/base.py
    lines: 78-89
  - path: src/acheron/shell/stores/memory.py
    lines: 92-107
  - path: src/acheron/shell/stores/redis.py
    lines: 153-173
  - path: src/acheron/shell/stores/redis.py
    lines: 236-284
  - path: src/acheron/shell/stores/redis.py
    lines: 551-566
  - path: src/acheron/shell/api/routes/workers.py
    lines: 22-29
  - path: src/acheron/shell/api/routes/workers.py
    lines: 74-101
  - path: dashboard/booting_progress.py
    lines: 22-54
  - path: dashboard/app.py
    lines: 15-22
  - path: dashboard/templates/partials/workers.html
    lines: 13-20
  - path: dashboard/templates/index.html
    lines: 62-87
related: []
fixed_in: [pending]
verified_in: []
last_verified_at: {}
verified_by: ""
incident_ref: TBD-pagerduty
---
```

**Issue.** The 600-second BOOTING timeout remains an explicit health-monitor policy, but its lifecycle timestamp is now persisted and the warning/timeout transitions are observable. The monitor re-reads the store after entering BOOTING, emits one warning per `(worker_id, booting_since)` lifecycle at 540 seconds, and records `provider BOOTING timeout exceeded` before transitioning to OFFLINE at 600 seconds.

**Why it matters.** RunPod cold starts are nominally < 2 min; 600s is generous. A worker that does not cold-start in 10 min is usually a misconfigured endpoint, and operators need a persisted breadcrumb and warning before recovery action.

**Recommendation.** Keep the BOOTING timestamp atomic across memory and Redis store transitions, expose clamped elapsed/timeout values through the worker API, and render the same whole-second contract in the dashboard helper and one-second timer. Clear the timestamp on HEALTHY, OFFLINE, and timeout transitions.

**Verification.** Focused store and health-monitor tests cover persistence, concurrent transition invariants, warning keying across same-ID lifecycles, wall-clock clamping, and timeout behavior. Worker-route, dashboard-helper, and template tests cover `BOOTING` elapsed/timeout output and one-second browser wiring; full repository and first-run gates also exercise the unchanged route/auth/TLS/Compose/non-root contracts.

## MAINT-010 — Worker re-registration inherits stale state

```yaml
---
id: MAINT-010
title: "Worker re-registration inherits stale state — `register()` overwrites the worker hash but does not reset `consecutive_failures`, `status`, or `last_error` from the previous lifecycle"
status: open
severity: medium
effort: S
discovered_via: [on-call, code-review]
user_facing_surface: internal
silent: true
journey_stage: t2
user_journey: "Worker `qwen3tts-1` restarts after a 30-min outage. Orchestrator's `POST /workers` overwrites `endpoint`, `transport`, `capabilities_json`, `metadata_json` but leaves `consecutive_failures=2`, `status=BOOTING`, `last_error='gRPC status UNAVAILABLE'`. The next health probe is a failure, hits the 3-failure threshold, and the worker is unregistered before the operator even sees the BOOTING state."
files:
  - path: src/acheron/shell/stores/redis.py
    lines: 441-455
  - path: src/acheron/shell/stores/redis.py
    lines: 212-227
related: []
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
incident_ref: TBD-pagerduty
---
```

**Issue.** `RedisWorkerStore.register` overwrites only the keys in `_worker_fields`. The `last_health_check` and any pre-existing fields not in `_worker_fields` survive. Re-registration is silent — the orchestrator logs `Registered worker %s` but no "state was reset" breadcrumb.

**Why it matters.** Re-registration is the routine on-call action when a worker restarts.

**Recommendation.** `RedisWorkerStore.register` deletes the old hash before re-writing: `pipe.delete(key); pipe.hset(mapping=fields)`. The orchestrator logs `INFO worker %s re-registered, state reset (prev last_health_check=%s, prev consecutive_failures=%d)`.

**Verification.** Worker A registers with `consecutive_failures=2`, `status=BOOTING`. Worker A re-registers 30s later. The Redis hash has `consecutive_failures='0'`, `status=HEALTHY`, `last_health_check=t1+30s`.

## MAINT-011 — `last_error` is wiped on first successful probe

```yaml
---
id: MAINT-011
title: "`last_error` is wiped on the first successful probe — the error trail that caused `record_health_failure` to unregister the worker is gone before the on-call can read it"
status: open
severity: high
effort: M
discovered_via: [on-call, code-review]
user_facing_surface: dashboard
silent: true
journey_stage: t2
user_journey: "Worker fails 3 health checks in a row; `last_error` accumulates to `ConnectError: Connection refused`. Worker recovers; next probe is HEALTHY; `record_health_success` overwrites `last_error` to `''` AND sets `status=HEALTHY`. On-call opens the dashboard to see the recovered worker; the row shows `HEALTHY — last error: (none)`. The 3 failures that just happened are gone."
files:
  - path: src/acheron/shell/stores/redis.py
    lines: 510-518
related: [OBS-007]
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
incident_ref: TBD-pagerduty
---
```

**Issue.** `record_health_success` resets `consecutive_failures='0'`, `last_health_check=now`, `status=HEALTHY`, and `last_error=''`. The on-call at 2am investigating "why did this worker just go HEALTHY after 3 failures" has no breadcrumb.

**Why it matters.** A single error string isn't enough — the on-call needs a small history.

**Recommendation.** Replace the single `last_error` field with `error_history: list[dict]` — append-only, capped at 10 entries.

**Verification.** Worker fails 3 times. Worker recovers. `GET /workers/qwen3tts-1` returns `error_history=[{ts:t1, error:ConnectError, consecutive_failures:1}, ...]`.

## MAINT-012 — `ACHERON_DATA_DIR` grows monotonically, no `acheron cleanup`

```yaml
---
id: MAINT-012
title: "`ACHERON_DATA_DIR` grows monotonically with no auto-pruning and no `acheron cleanup` command — `_verify_data_dir_writable` checks writability, not capacity"
status: open
severity: high
effort: M
discovered_via: [on-call, code-review, audit]
user_facing_surface: cli
silent: true
journey_stage: t2
user_journey: "On-call at 2am gets paged: `POST /jobs` returns 500 with `Data dir /data/jobs is not writable: [Errno 28] No space left on device`. Engineer runs `acheron cleanup --keep-successful 7d --keep-failed 30d --dry-run`; the CLI prints the 23 jobs that would be pruned (8.2G reclaimable) and prompts `--apply`."
files:
  - path: src/acheron/shell/orchestrator.py
    lines: 142-158
  - path: src/acheron/shell/orchestrator.py
    lines: 233-253
related: []
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
incident_ref: TBD-pagerduty
---
```

**Issue.** `_verify_data_dir_writable` creates the data dir, writes a probe file, reads it back, and raises `AcheronError` only if `OSError` fires. It does NOT check `shutil.disk_usage` — a full-volume writes succeed (writability is fine) but the underlying disk is at 99% used. The step cache accumulates per-job plan results with no pruning.

**Why it matters.** At 2am the orchestrator's `start()` succeeds (the dir is writable, just full); the first `POST /jobs` after the fill fails with a cryptic error.

**Recommendation.** `_verify_data_dir_writable` also calls `shutil.disk_usage(data_dir)` and emits WARNING at < 10% free, ERROR at < 5% free. The orchestrator gains a `cleanup(keep_successful, keep_failed, dry_run)` method. `acheron cleanup --keep-successful 7d --keep-failed 30d [--dry-run]` is the CLI front.

**Verification.** A data dir with 200 jobs. `acheron cleanup --keep-successful 7d --keep-failed 30d --dry-run` prints the 23 jobs that match. `acheron cleanup ... --apply` deletes them; `du -sh /data/jobs` shows the reduction.

## MAINT-013 — `bind_request_id` populates log but CLI never echoes the request_id

```yaml
---
id: MAINT-013
title: "`bind_request_id` injects a UUID into every orchestrator log line but the CLI never echoes the request_id back to the operator — the on-call at 2am has the job_id, not the request_id"
status: open
severity: medium
effort: S
discovered_via: [on-call, code-review]
user_facing_surface: cli
silent: true
journey_stage: t2
user_journey: "On-call submits `acheron job submit ...`; the CLI prints `job_id=job-abcd1234` and exits 0. The orchestrator's request handler at `api/app.py:87-90` minted a request_id `req-9f8e7d6c` and bound it to the log context, so every log line for this submission has `request_id=req-9f8e7d6c` — but the CLI never received the `x-request-id` response header."
files:
  - path: src/acheron/shell/api/app.py
    lines: 86-90
related: [OPS-003]
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
incident_ref: TBD-pagerduty
---
```

**Issue.** The `_request_id_middleware` reads `x-request-id` from the request, generates a UUID if absent, binds it via `bind_request_id(request_id)`, and the `ContextFilter` injects it into every log line. The CLI uses `httpx` without surfacing the `x-request-id` response header.

**Why it matters.** Log correlation by `request_id` is the on-call's breadcrumb.

**Recommendation.** The CLI's `httpx` client captures `response.headers['x-request-id']` and prints it to stderr at the end of every command: `request_id=req-9f8e7d6c`.

**Verification.** `acheron job submit ...` exits 0 and prints `request_id=req-...` to stderr alongside `job_id=job-...`.

## MAINT-014 — `uninterruptablePrice` is the lowest available rate, not what was paid

```yaml
---
id: MAINT-014
title: "`RunPodPrice.estimate` returns the lowest *uninterruptable* community-cloud price — the rate at which the job actually ran (potentially secure-cloud at 2x) is never recorded"
status: open
severity: high
effort: S
discovered_via: [on-call, code-review, audit]
user_facing_surface: dashboard
silent: true
journey_stage: t2
user_journey: "On-call at 2am reconciles a $47 RunPod bill against the dashboard's cost column showing $23. The dashboard's rate is `uninterruptablePrice` for community cloud at $0.69/hr (the *lowest* available); RunPod actually billed the job at $2.49/hr for secure cloud."
files:
  - path: src/acheron/worker_sdk/pricing.py
    lines: 58-63
  - path: src/acheron/worker_sdk/pricing.py
    lines: 158-160
  - path: src/acheron/worker_sdk/pricing.py
    lines: 214-231
related: [MAINT-002, CORR-040]
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
incident_ref: TBD-pagerduty
---
```

**Issue.** `_GraphQLLowestPrice.uninterruptable_price` is the lowest *uninterruptable* price RunPod offers for the GPU type at the given secure-cloud flag, not the rate the job actually ran at. A job that ran on a secure-cloud H100 (~$2.49/hr) is reported at the community-cloud L4 rate (~$0.69/hr) if the worker is configured with `secure_cloud: false`.

**Why it matters.** The on-call reconciling the RunPod bill finds a 2-3x discrepancy with no per-job indicator of which cloud the rate applies to.

**Recommendation.** Record per-job: `rate_source: enum(uninterruptable_lowest, on_demand_actual, cached, static, zero)`, `rate_at_job_start: float`, `secure_cloud: bool`, `gpu_type: str`.

**Verification.** A job that ran on a secure-cloud H100 endpoint shows `rate_source=on_demand_actual rate=2.49 gpu_type=H100 secure_cloud=true`.

## MAINT-015 — `price_source: zero` silently disables cost tracking

```yaml
---
id: MAINT-015
title: "`price_source: zero` silently disables cost tracking — every stub worker in `docker-compose.yml` defaults to it, and the basis badge says `STATIC` not `STATIC (zero, stub/local)`"
status: open
severity: high
effort: S
discovered_via: [on-call, code-review, audit]
user_facing_surface: dashboard
silent: true
journey_stage: t2
user_journey: "On-call at 2am gets paged: 'your cost is $0, are we being billed?'. Engineer opens the dashboard, sees a job with `cost=$0.00, basis=STATIC`. The basis badge is correct (`STATIC`) but the *reason* is `zero (stub/local)` — the worker is configured with `PRICE_SOURCE=zero` (the default in `docker-compose.yml:98`). The on-call cannot tell the difference between 'this worker is local/stub' and 'this worker is configured with `STATIC $0.10/hr`'."
files:
  - path: src/acheron/worker_sdk/pricing.py
    lines: 75-89
  - path: src/acheron/worker_sdk/pricing.py
    lines: 122-141
  - path: docker-compose.yml
    lines: 92-104
related: [MAINT-002]
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
incident_ref: TBD-pagerduty
---
```

**Issue.** `ZeroPrice.estimate` returns `PriceEstimate(cost=0.0, reason="zero (stub/local)")`. `to_cost_basis` maps `reason="zero (stub/local)"` to `CostBasis.STATIC`. The dashboard renders `STATIC` and the on-call cannot tell whether the zero is "zero, by design" (stub) or "zero, by configuration" (a worker explicitly configured with $0/hr).

**Why it matters.** A production worker that someone accidentally configured with `PRICE_SOURCE=zero` silently disables tracking.

**Recommendation.** Add `CostBasis.STUB` (or extend `STATIC` to a 2-tuple of `(basis, sub_kind: zero|configured)`). The dashboard's cost basis legend gains a `STUB` entry.

**Verification.** A stub worker (configured with `PRICE_SOURCE=zero`) submits a job. The dashboard shows `basis=STUB`. A production worker (configured with `PRICE_SOURCE=static, DOLLARS_PER_HOUR=0.10`) shows `basis=STATIC` with sub-kind `configured`.

## MAINT-016 — Dashboard does not surface the running image SHA / version pin

```yaml
---
id: MAINT-016
title: "Dashboard does not surface the running image SHA / version pin — operator at 2am cannot tell which `ghcr.io/...:sha-abc1234` is deployed"
status: open
severity: high
effort: M
discovered_via: [on-call, audit]
user_facing_surface: dashboard
silent: true
journey_stage: t2
user_journey: "On-call at 2am gets paged: 'regression in worker response format — what version are we on?'. Engineer opens the dashboard; the header shows `Acheron v1.4.2 (build sha-abc1234 2026-07-23 14:22 UTC, branch master, dirty=False)`."
files:
  - path: src/acheron/shell/api/app.py
    lines: 97-99
  - path: dashboard/app.py
    lines: 1-50
related: []
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
incident_ref: TBD-pagerduty
---
```

**Issue.** The orchestrator's `/health` endpoint returns `{"status": "ok"}` — no version, no build SHA, no image tag. The dashboard's header does not surface any of this.

**Why it matters.** Rollback decisions are made on the live system's identity. Without a `/version` endpoint, the on-call's only path is `docker inspect`.

**Recommendation.** Add `GET /version` returning `{image, sha, build_time, branch, dirty, version, registry}`. The dashboard header shows `vX.Y.Z (sha-abc1234)`.

**Verification.** A orchestrator image built with build-arg GIT_SHA=abc1234. `GET /version` returns `{sha:abc1234, ...}`. The dashboard header shows `v1.4.2 (sha-abc1234)`.

## MAINT-017 — HF cache + `HF_HUB_OFFLINE=1` + model_id change = silent wrong-weights load

```yaml
---
id: MAINT-017
title: "`HF_HUB_OFFLINE=1` + stale cache = silent wrong weights — switching `TRANSLATEGEMMA_MODEL_ID` from 12b to 4b leaves the old 12b snapshot on disk and the new worker loads it without a checksum or path validation"
status: open
severity: high
effort: M
discovered_via: [on-call, audit, code-review]
user_facing_surface: worker-image
silent: true
journey_stage: t2
user_journey: "Operator switches `TRANSLATEGEMMA_MODEL_ID` from `google/translategemma-12b-it` to `google/translategemma-4b-it` to cut GPU cost. The new edge image starts with `HF_HUB_OFFLINE=1`; the volume's `/runpod-volume/huggingface-cache/hub/` still has `models--google--translategemma-12b-it/snapshots/<sha>/` from the old run. The worker resolves the model id against the cache, finds the 12b snapshot, loads it silently. Output quality degrades."
files:
  - path: docker-compose.yml
    lines: 241-274
  - path: workers/translategemma/handler.py
    lines: 125-148
related: [CFG-007, CFG-008]
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
incident_ref: TBD-pagerduty
---
```

**Issue.** The translategemma handler reads `TRANSLATEGEMMA_MODEL_ID` and resolves weights via Hugging Face's offline cache layout. When the operator changes the env var, the cache directory is not cleaned, and the new `model_id` may or may not exist in the cache.

**Why it matters.** Model ID changes are routine. The operator's mental model is "I changed the env var, the worker loads the new model". The actual behavior is silent wrong-weights loading.

**Recommendation.** Workers emit a structured `startup_log` line: `{model_id, resolved_snapshot_sha, snapshot_path, files_loaded}`. The orchestrator's `POST /workers` includes the loaded `model_id` in `capabilities.metadata`; the dashboard shows it in the worker row.

**Verification.** Worker A starts with `MODEL_ID=12b`. Operator changes env var to `4b`, restarts. Worker B's startup log shows `model_id=4b resolved_snapshot_sha=<4b-snapshot>`. The orchestrator's `GET /workers/<id>` returns the model_id. If the 4b snapshot is missing, the worker fails to start with a clear error.
