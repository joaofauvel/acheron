---
theme: MAINT
last_updated_date: 2026-08-02
version: 5
---

# MAINT

**Grade**: C (1 high-severity open story; 4 additional stale stories remain)
**Calibration target**: an on-call engineer should be able to recover from a 2am page without paging someone else.

## MAINT-001 — No admin endpoints to reap stuck `RUNNING` jobs

```yaml
---
id: MAINT-001
title: "`shell/api/routes/` has no admin namespace; on-call cannot reap / mark-failed / drain stuck jobs after orchestrator restart"
status: verified
severity: high
effort: M
discovered_via: [on-call, code-review]
user_facing_surface: cli
silent: true
journey_stage: t2
user_journey: "On-call engineer SSHes in after a 2am page: orchestrator was `kill -9` during a deploy, restarted, finds 7 jobs in `status=RUNNING` with no active task. Engineer runs `acheron admin reap-stuck --older-than 60s --reason orphaned_by_restart`, sees 7 jobs reaped, each marked FAILED with the reason; dashboard shows the per-job FAILED rows within 5s."
files:
  - path: src/acheron/shell/orchestrator.py
    lines: 353-419
  - path: src/acheron/shell/api/routes/admin.py
    lines: 58-225
related: [OBS-001, OBS-014, OBS-015]
fixed_in: [6d47e35, CURRENT_HEAD]
verified_in: [6d47e35, CURRENT_HEAD]
last_verified_at:
  commit: CURRENT_HEAD
  tree: 05d842cb3514231ddab1f3591c9a71e23cdd0cb77eaede35f49ec955487ae2fc
  date: "2026-08-02"
verified_by: "harness:phase-4d-task-10-recovery"
incident_ref: TBD-pagerduty
---
```

**Issue.** The orchestrator's recovery paths are in-process. A `kill -9` skips the lifespan, leaving in-flight jobs in `RUNNING` state in Redis. The shell API now exposes cancellation, retry, preview, logs, resume, and label filtering, but still has no `/admin` namespace for reaping or marking orphaned jobs.

**Why it matters.** On-call still needs a supported recovery path for jobs orphaned by an orchestrator restart.

**Recommendation.** Add a `/admin` namespace: `POST /admin/jobs/{id}/mark-failed`, `POST /admin/jobs/reap-stale?older-than=60s`, `POST /admin/drain`, and `POST /admin/rotate-token`.

**Verification.** With 3 stuck jobs, `acheron admin reap-stuck --older-than 60s` returns 200 with `{ reaped: 3, job_ids: [...] }`.

## MAINT-002 — Failed job cost row doesn't show GPU / cache age

```yaml
---
id: MAINT-002
title: "When a job is FAILED with `gpu_seconds` recorded, the cost row doesn't show which GPU the rate was queried for, or how stale it is"
status: verified
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
    lines: 69-75
related: [CORR-008, CORR-040, TYPE-005]
fixed_in: [d78e7a1, CURRENT_HEAD]
verified_in: [d78e7a1, CURRENT_HEAD]
last_verified_at:
  commit: CURRENT_HEAD
  tree: 05d842cb3514231ddab1f3591c9a71e23cdd0cb77eaede35f49ec955487ae2fc
  date: "2026-08-02"
verified_by: "harness:pricing-outage+gpu-switch+failed-job-integration"
incident_ref: TBD-pagerduty
---
```

**Issue.** The cost basis enum is rendered in the dashboard, but the underlying data (GPU type, query timestamp, cache age) is not surfaced. The on-call investigating a failed job with `cost=$0.34` cannot tell whether the rate is for an L4 or an H100, cannot tell whether the cache was 4 minutes stale or 4 hours stale.

**Why it matters.** Cost forensics is part of the on-call's job.

**Recommendation.** Extend `PriceEstimate` with `gpu_type: str | None`, `queried_at: datetime | None`, `cache_age_seconds_at_estimate: float | None`. Surface in `JobResponse` (or a `cost_breakdown` sub-object).

**Verification.** The failed-job integration test persists GPU seconds, worker identity, rate basis, GPU type, and cache age; focused API/CLI/dashboard tests cover the explanation surfaces and both simulations provide outage metadata evidence.

## MAINT-003 — Cert expiry is silent — no warning at 30/7/0 days remaining

```yaml
---
id: MAINT-003
title: "Cert expiry is silent — orchestrator reads `ACHERON_TLS_CERT_FILE` once and emits no warning at 30/7/0 day marks"
status: verified
severity: high
effort: M
discovered_via: [on-call, code-review, audit]
user_facing_surface: certs
silent: true
journey_stage: t2
user_journey: "On-call at 2am is paged because all worker → orchestrator gRPC calls fail. Engineer runs `acheron certs status` and sees `orchestrator.crt expires in 0d 0h 14m`, plus warnings emitted to the orchestrator's log at the 30/7/0 day marks; rotates the cert via `acheron certs reload`, workers reconnect within 5s."
files:
  - path: src/acheron/tls.py
    lines: 28-180
  - path: src/acheron/shell/api/routes/admin.py
    lines: 82-111
  - path: src/acheron/cli.py
    lines: 698-709
  - path: scripts/generate_dev_certs.py
    lines: 209-247
related: []
bundle: 01-cert-tls
fixed_in: [7fb2631, 62c088a, e85861b]
verified_in: [CURRENT_HEAD]
last_verified_at:
  commit: CURRENT_HEAD
  tree: 05d842cb3514231ddab1f3591c9a71e23cdd0cb77eaede35f49ec955487ae2fc
  date: "2026-08-02"
verified_by: "independent:docs/superpowers/review/bundle-01-cert-tls-independent-verification.md"
incident_ref: TBD-pagerduty
---
```

**Issue.** The orchestrator's TLS layer previously read `ACHERON_TLS_CERT_FILE` once at process start and never re-read it. There was no expiry check, `not_after` introspection, threshold warning, or `/admin/certs/status` endpoint.

**Current state.** `CertificateManager` reports subject, expiry, remaining time, and severity; logs startup status and one message at each 30-day, 7-day, 1-day, and expiry threshold; and the admin API/CLI expose sanitized status. The Compose development bundle remains separate from production certificate management.

**Verification.** Independent status → replacement → reload → same-PID → healthy API → HTTP worker-connectivity verification passed in `docs/superpowers/review/bundle-01-cert-tls-independent-verification.md`; automated TLS, admin, CLI, Compose, and first-run gates also pass.

## MAINT-004 — Dev cert SAN list breaks production TLS verify

```yaml
---
id: MAINT-004
title: "Dev cert SAN list is `localhost` / `127.0.0.1` only — production deploy with the orchestrator's real hostname fails TLS verify on every worker handshake"
status: obsolete
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

**Resolution.** The production deployment documentation already directs operators to mount externally managed certificates with the correct SANs and states that no Acheron code change is required. `just certs` is documented as a development-only generator, so the production hostname journey in this story is not a supported workflow. Keep the concern closed unless operator-supplied SAN management for dev or staging becomes an explicit product requirement.

## MAINT-005 — Cert rotation requires orchestrator restart

```yaml
---
id: MAINT-005
title: "Cert rotation requires orchestrator restart — `uvicorn` does not reload `ssl_certfile` / `ssl_keyfile` after a SIGHUP, and the operator has no `/admin/certs/reload` endpoint"
status: verified
severity: medium
effort: M
discovered_via: [on-call, code-review]
user_facing_surface: certs
silent: true
journey_stage: t2
user_journey: "On-call rotates `orchestrator.crt` and `orchestrator.key` on disk after the 30-day warning fires (MAINT-003). Operator runs `acheron certs reload`; the orchestrator's `uvicorn` server reloads the cert/key without bouncing (no downtime); workers continue to connect."
files:
  - path: src/acheron/tls.py
    lines: 51-180
  - path: src/acheron/shell/api/routes/admin.py
    lines: 114-135
  - path: src/acheron/shell/api/__main__.py
    lines: 18-21
  - path: src/acheron/worker_sdk/_server.py
    lines: 42-50
  - path: src/acheron/cli.py
    lines: 712-719
related: [MAINT-003]
bundle: 01-cert-tls
fixed_in: [9ec88c9, 134c47d]
verified_in: [CURRENT_HEAD]
last_verified_at:
  commit: CURRENT_HEAD
  tree: 05d842cb3514231ddab1f3591c9a71e23cdd0cb77eaede35f49ec955487ae2fc
  date: "2026-08-02"
verified_by: "independent:docs/superpowers/review/bundle-01-cert-tls-independent-verification.md"
incident_ref: TBD-pagerduty
---
```

**Issue.** `uvicorn_ssl_kwargs` was consumed once at process start, so `ssl_certfile` / `ssl_keyfile` were not hot-reloadable. There was no `/admin/certs/reload` HTTP endpoint or `acheron certs reload` CLI command.

**Current state.** The admin endpoint validates the replacement pair, updates the persistent server context, and records an admin action; the CLI uses the admin token and reports the sanitized result. The independent status → replacement → reload → same-PID → healthy API → HTTP worker-connectivity journey passed; plain HTTP remains available when TLS is unset.

**Verification.** Automated TLS integration, admin, CLI, `just validate`, and `just first-run --step 2` gates provide implementation evidence. The independent status → replacement → reload → same-PID → worker-connectivity journey passed; see `docs/superpowers/review/bundle-01-cert-tls-independent-verification.md`.

## MAINT-006 — Registration-token auto-mint is unreachable in compose

```yaml
---
id: MAINT-006
title: "Registration-token auto-mint is unreachable in Compose"
status: stale
severity: medium
effort: S
discovered_via: [on-call, first-run, code-review]
user_facing_surface: compose
silent: true
journey_stage: t2
user_journey: "Deployer on a fresh checkout follows the README and runs `docker compose up`. Compose refuses to start: `ACHERON_REGISTRATION_TOKEN must be set`. Deployer must run `openssl rand -hex 32` manually, set the env var in `.env`, and re-run `docker compose up`. The orchestrator's documented 'auto-generates and persists' path is never reached because compose requires the env var up front."
files:
  - path: docker-compose.yml
    lines: 45-49
  - path: src/acheron/shell/orchestrator.py
    lines: 564-601
related: []
bundle: 02-token-auth
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
drift_note: "Compose still requires a token during interpolation, while auto-mint persists to <data_dir>/.registration_token; worker profiles receive a static startup token."
incident_ref: TBD-pagerduty
---
```

**Issue.** `docker-compose.yml:48` declares `ACHERON_REGISTRATION_TOKEN: ${ACHERON_REGISTRATION_TOKEN:?ACHERON_REGISTRATION_TOKEN must be set}`, so Compose aborts before the orchestrator reaches `_load_or_create_registration_token` at `src/acheron/shell/orchestrator.py:564-601`. The auto-mint branch is therefore unreachable in the supported Compose deployment.

**Why it matters.** The auto-mint path is the documented on-ramp for a fresh deploy.

**Recommendation.** Change `docker-compose.yml:48` from `${VAR:?...}` to `${VAR:-}` so compose starts with the env var unset, and the orchestrator's auto-mint path fires. Persist the minted token; the workers read the same path via a shared volume.

**Verification.** With the project token unset, `docker compose up` starts cleanly, the orchestrator persists a 32-character token at `<data_dir>/.registration_token` ( `/data/jobs/.registration_token` in Compose), and every worker edge registers with the same value.

## MAINT-007 — No `acheron token rotate` command and no audit trail

```yaml
---
id: MAINT-007
title: "No safe registration-token rotation or audit trail; rotating the shared token requires updating and restarting every worker edge"
status: stale
severity: high
effort: M
discovered_via: [on-call, code-review, audit]
user_facing_surface: cli
silent: true
journey_stage: t2
user_journey: "On-call at 2am checks the registration-token status and sees its creation time and rotation history. Engineer runs `acheron token rotate --reason incident-2026-07-24-worker-401`; the new token is recorded under `<data_dir>/.registration_token`, every worker edge receives the new `ACHERON_WORKER__REGISTRATION_TOKEN` and is restarted or reloaded, and a health check confirms dispatch still succeeds."
files:
  - path: src/acheron/shell/orchestrator.py
    lines: 574-601
  - path: src/acheron/shell/api/routes/admin.py
    lines: 57-225
  - path: src/acheron/cli.py
    lines: 603-686
  - path: src/acheron/worker_sdk/app.py
    lines: 104-121
  - path: src/acheron/worker_sdk/_edge_http.py
    lines: 529-540
  - path: docker-compose.yml
    lines: 48-61
related: [MAINT-006, SEC-008]
bundle: 02-token-auth
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
incident_ref: TBD-pagerduty
---
```

**Issue.** Auto-minted tokens are persisted at `<data_dir>/.registration_token` without creation metadata or rotation history. There is no token status or rotation workflow, and Compose supplies a static token to each edge at startup (`docker-compose.yml:48,83,112`). Worker registration and execution validate that startup token (`src/acheron/worker_sdk/app.py:104-121`; `_edge_http.py:529-540`), so changing the orchestrator-side file alone would not rotate the shared credential.

**Why it matters.** Token rotation is a security-incident response procedure. A partial rotation can strand worker edges with an old credential or interrupt dispatch without an auditable record.

**Recommendation.** Provide an admin-protected token status and rotation workflow with creation metadata and audit history. It must handle both environment-supplied and file-backed tokens and coordinate distribution plus restart or reload of every worker edge before retiring the old token.

**Verification.** `acheron token rotate --reason "test"` records a new token and an audit entry under the configured data directory; all worker edges receive the new credential, re-register or reload successfully, and a test job dispatches after rotation.

## MAINT-008 — No "stuck > N minutes" filter in `list_jobs`

```yaml
---
id: MAINT-008
title: "No \"stuck > N minutes\" filter in `list_jobs` — the on-call at 2am cannot find the 3 stuck jobs among 200 in the dashboard's flat list"
status: verified
severity: high
effort: S
discovered_via: [on-call, code-review]
user_facing_surface: dashboard
silent: true
journey_stage: t2
user_journey: "On-call at 2am opens the dashboard, sees 200 jobs (190 SUCCESS, 3 FAILED, 7 RUNNING). Engineer clicks the 'Stuck' filter, sets 'Older than 30 min', and the list collapses to the 3 RUNNING jobs whose `submitted_at` is > 30 min ago."
files:
  - path: src/acheron/shell/orchestrator.py
    lines: 1029-1031
  - path: src/acheron/shell/api/routes/job_lifecycle.py
    lines: 73-97
related: [MAINT-001]
fixed_in: [6d47e35, CURRENT_HEAD]
verified_in: [6d47e35, CURRENT_HEAD]
last_verified_at:
  commit: CURRENT_HEAD
  tree: 05d842cb3514231ddab1f3591c9a71e23cdd0cb77eaede35f49ec955487ae2fc
  date: "2026-08-02"
verified_by: "harness:phase-4d-task-10-recovery"
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
    lines: 74-95
  - path: dashboard/booting_progress.py
    lines: 22-54
  - path: dashboard/app.py
    lines: 15-22
  - path: dashboard/templates/partials/workers.html
    lines: 13-20
  - path: dashboard/templates/index.html
    lines: 62-87
related: []
fixed_in: [143120a]
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
status: verified
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
fixed_in: [6d47e35, CURRENT_HEAD]
verified_in: [6d47e35, CURRENT_HEAD]
last_verified_at:
  commit: CURRENT_HEAD
  tree: 05d842cb3514231ddab1f3591c9a71e23cdd0cb77eaede35f49ec955487ae2fc
  date: "2026-08-02"
verified_by: "harness:phase-4d-task-10-recovery"
incident_ref: TBD-pagerduty
---
```

**Resolution.** `_worker_fields()` now resets `consecutive_failures`, `status`, `last_error`, and `booting_since` during worker re-registration. The recovered registration path is covered by the Task 10 recovery journey.

**Verification.** Re-register a worker after failures and confirm its health state starts clean.

## MAINT-011 — `last_error` is wiped on first successful probe

```yaml
---
id: MAINT-011
title: "`last_error` is wiped on the first successful probe — the error trail that caused `record_health_failure` to unregister the worker is gone before the on-call can read it"
status: verified
severity: high
effort: M
discovered_via: [on-call, code-review]
user_facing_surface: dashboard
silent: true
journey_stage: t2
user_journey: "Worker fails 3 health checks in a row; `last_error` accumulates to `ConnectError: Connection refused`. Worker recovers; next probe is HEALTHY; `record_health_success` overwrites `last_error` to `''` AND sets `status=HEALTHY`. On-call opens the dashboard to see the recovered worker; the row shows `HEALTHY — last error: (none)`. The 3 failures that just happened are gone."
files:
  - path: src/acheron/shell/stores/redis.py
    lines: 592-601
related: [OBS-007]
fixed_in: [6d47e35, CURRENT_HEAD]
verified_in: [6d47e35, CURRENT_HEAD]
last_verified_at:
  commit: CURRENT_HEAD
  tree: 05d842cb3514231ddab1f3591c9a71e23cdd0cb77eaede35f49ec955487ae2fc
  date: "2026-08-02"
verified_by: "harness:phase-4d-task-10-recovery"
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
status: verified
severity: high
effort: M
discovered_via: [on-call, code-review, audit]
user_facing_surface: cli
silent: true
journey_stage: t2
user_journey: "On-call at 2am gets paged: `POST /jobs` returns 500 with `Data dir /data/jobs is not writable: [Errno 28] No space left on device`. Engineer runs `acheron cleanup --keep-successful 7d --keep-failed 30d --dry-run`; the CLI prints the 23 jobs that would be pruned (8.2G reclaimable) and prompts `--apply`."
files:
  - path: src/acheron/shell/orchestrator.py
    lines: 211-227
related: []
fixed_in: [6d47e35, CURRENT_HEAD]
verified_in: [6d47e35, CURRENT_HEAD]
last_verified_at:
  commit: CURRENT_HEAD
  tree: 05d842cb3514231ddab1f3591c9a71e23cdd0cb77eaede35f49ec955487ae2fc
  date: "2026-08-02"
verified_by: "harness:phase-4d-task-10-recovery"
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
status: verified
severity: medium
effort: S
discovered_via: [on-call, code-review]
user_facing_surface: cli
silent: true
journey_stage: t2
user_journey: "On-call submits `acheron job submit ...`; the CLI prints `job_id=job-abcd1234` and exits 0. The orchestrator's request handler at `api/app.py:87-90` minted a request_id `req-9f8e7d6c` and bound it to the log context, so every log line for this submission has `request_id=req-9f8e7d6c` — but the CLI never received the `x-request-id` response header."
files:
  - path: src/acheron/shell/api/app.py
    lines: 88-92
related: [OPS-003]
fixed_in: [8357163c, 3f5b537, 8c0f119, CURRENT_HEAD]
verified_in: [16898af, CURRENT_HEAD]
last_verified_at:
  commit: CURRENT_HEAD
  tree: 05d842cb3514231ddab1f3591c9a71e23cdd0cb77eaede35f49ec955487ae2fc
  date: "2026-08-02"
verified_by: "harness:phase-4d-task-12-correlation"
incident_ref: TBD-pagerduty
---
```

**Current state.** The API middleware returns the request ID on every response, and the API client/CLI print the captured ID to stderr for both regular and streaming commands. The dashboard also renders the version-fetch correlation ID when available.

**Why it matters.** Log correlation by `request_id` is the on-call's breadcrumb.

**Verification.** Task 12 focused client and CLI tests cover generated and caller-supplied IDs, regular and streaming responses, stderr output, and missing-header behavior. Task 13 dashboard tests cover safe request-ID rendering alongside the deployed version.

## MAINT-014 — `uninterruptablePrice` is the lowest available rate, not what was paid

```yaml
---
id: MAINT-014
title: "`RunPodPrice.estimate` returns the lowest *uninterruptable* community-cloud price — the rate at which the job actually ran (potentially secure-cloud at 2x) is never recorded"
status: verified
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
fixed_in: [d78e7a1, CURRENT_HEAD]
verified_in: [d78e7a1, CURRENT_HEAD]
last_verified_at:
  commit: CURRENT_HEAD
  tree: 05d842cb3514231ddab1f3591c9a71e23cdd0cb77eaede35f49ec955487ae2fc
  date: "2026-08-02"
verified_by: "harness:gpu-switch+runpod-contract-tests"
incident_ref: TBD-pagerduty
---
```

**Issue.** `_GraphQLLowestPrice.uninterruptable_price` is the lowest *uninterruptable* price RunPod offers for the GPU type at the given secure-cloud flag, not the rate the job actually ran at. A job that ran on a secure-cloud H100 (~$2.49/hr) is reported at the community-cloud L4 rate (~$0.69/hr) if the worker is configured with `secure_cloud: false`.

**Why it matters.** The on-call reconciling the RunPod bill finds a 2-3x discrepancy with no per-job indicator of which cloud the rate applies to.

**Recommendation.** Record per-job: `rate_source: enum(uninterruptable_lowest, on_demand_actual, cached, static, zero)`, `rate_at_job_start: float`, `secure_cloud: bool`, `gpu_type: str`.

**Verification.** The GPU-switch simulation records a refreshed A40 identity and secure-cloud $2.49/hr quote, then proves outage fallback is CACHED with retained identity/rate metadata; provider quotes remain explicitly non-invoice UNKNOWN when fresh.

## MAINT-015 — `price_source: zero` silently disables cost tracking

```yaml
---
id: MAINT-015
title: "`price_source: zero` silently disables cost tracking — every stub worker in `docker-compose.yml` defaults to it, and the basis badge says `STATIC` not `STATIC (zero, stub/local)"
status: verified
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
fixed_in: [d78e7a1, CURRENT_HEAD]
verified_in: [d78e7a1, CURRENT_HEAD]
last_verified_at:
  commit: CURRENT_HEAD
  tree: 05d842cb3514231ddab1f3591c9a71e23cdd0cb77eaede35f49ec955487ae2fc
  date: "2026-08-02"
verified_by: "harness:pricing-outage+focused-tests"
incident_ref: TBD-pagerduty
---
```

**Issue.** `ZeroPrice.estimate` returns `PriceEstimate(cost=0.0, reason="zero (stub/local)")`. `to_cost_basis` maps `reason="zero (stub/local)"` to `CostBasis.STATIC`. The dashboard renders `STATIC` and the on-call cannot tell whether the zero is "zero, by design" (stub) or "zero, by configuration" (a worker explicitly configured with $0/hr).

**Why it matters.** A production worker that someone accidentally configured with `PRICE_SOURCE=zero` silently disables tracking.

**Recommendation.** Add `CostBasis.STUB` (or extend `STATIC` to a 2-tuple of `(basis, sub_kind: zero|configured)`). The dashboard's cost basis legend gains a `STUB` entry.

**Verification.** The pricing-outage simulation asserts `price_source=zero` is the only STUB path and distinguishes it from fresh RunPod UNKNOWN and warm-outage CACHED estimates; focused pricing tests retain STATIC as a separate basis.

## MAINT-016 — Dashboard does not surface the running image SHA / version pin

```yaml
---
id: MAINT-016
title: "Dashboard does not surface the running image SHA / version pin — operator at 2am cannot tell which `ghcr.io/...:sha-abc1234` is deployed"
status: verified
severity: high
effort: M
discovered_via: [on-call, audit]
user_facing_surface: dashboard
silent: true
journey_stage: t2
user_journey: "On-call at 2am gets paged: 'regression in worker response format — what version are we on?'. Engineer opens the dashboard; the header shows `Acheron v1.4.2 (build sha-abc1234 2026-07-23 14:22 UTC, branch master, dirty=False)`."
files:
  - path: src/acheron/shell/api/routes/version.py
    lines: 17-29
  - path: dashboard/app.py
    lines: 65-95
  - path: dashboard/templates/index.html
    lines: 41-45
  - path: dashboard/templates/partials/version.html
    lines: 1-2
  - path: dashboard/tests/test_dashboard.py
    lines: 116-153
related: []
fixed_in: [808353f, CURRENT_HEAD]
verified_in: [808353f, CURRENT_HEAD]
last_verified_at:
  commit: CURRENT_HEAD
  tree: 05d842cb3514231ddab1f3591c9a71e23cdd0cb77eaede35f49ec955487ae2fc
  date: "2026-08-02"
verified_by: "harness:phase-4d-task-13-dashboard-version"
incident_ref: TBD-pagerduty
---
```

**Current state.** `GET /version` returns the explicit build identity, and the dashboard fetches it without proxying configuration fields. The header renders only `vX.Y.Z (sha-abc1234)` plus the response correlation ID; failed fetches render `vunknown (sha-unknown)`.

**Why it matters.** Rollback decisions are made on the live system's identity. Operators need a safe live version pin without exposing deployment credentials or URLs.

**Verification.** Task 11 version-route tests cover explicit identity fields. Task 13 dashboard tests cover the deployed version/SHA, request ID, unknown fallback, and absence of image, branch, secret, and orchestrator URL values.

## MAINT-017 — HF cache + `HF_HUB_OFFLINE=1` + model_id change = silent wrong-weights load

```yaml
---
id: MAINT-017
title: "`HF_HUB_OFFLINE=1` + stale cache = silent wrong weights — switching `TRANSLATEGEMMA_MODEL_ID` from 12b to 4b leaves the old 12b snapshot on disk and the new worker loads it without a checksum or path validation"
status: obsolete
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

**Resolution.** The handler passes the configured model ID directly to Hugging Face loading and has no fallback to another model snapshot. The previously described silent old-model fallback is not present.

**Verification.** Change the configured model ID and confirm loading targets that ID rather than silently selecting another snapshot.

## MAINT-018 — Redis job schema has no upgrade path

```yaml
---
id: MAINT-018
title: "Redis job records have no upgrade path across schema changes"
status: stale
severity: high
effort: M
discovered_via: [code-review]
user_facing_surface: internal
silent: true
journey_stage: t2
user_journey: "An operator restarts the orchestrator after a schema deployment and lists an older persisted job; deserialization fails because the record lacks newly required fields, blocking recovery and visibility."
files:
  - path: src/acheron/shell/stores/redis.py
    lines: 557-580
  - path: src/acheron/shell/stores/redis.py
    lines: 708-733
related: []
bundle: 03-redis-schema
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
drift_note: "Job serialization and deserialization moved; the current loader still indexes required fields directly and has no persisted schema version or migration path."
---
```

**Issue.** Redis serialization has no persisted schema version (`src/acheron/shell/stores/redis.py:557-580`), while deserialization directly requires fields such as `progress`, `label`, and timestamps (`:708-733`). Representative older records therefore fail with `CacheCorruptedError` instead of remaining visible.

**Why it matters.** A deployment can make existing jobs unreadable, preventing operators from recovering or inspecting work already in progress.

**Recommendation.** Define an explicit persisted-schema version and migration/defaulting path for older Redis records.

**Verification.** Load a pre-change record after upgrade and confirm it is migrated or safely defaulted without losing job visibility.

## MAINT-019 — Completed job event buffers are never evicted

```yaml
---
id: MAINT-019
title: "Completed job event buffers are retained indefinitely"
status: obsolete
severity: medium
effort: M
discovered_via: [code-review]
user_facing_surface: internal
silent: true
journey_stage: t2
user_journey: "A long-running orchestrator completes many jobs; terminal event buffers remain retained after subscribers leave, and memory usage grows until the process is pressured or restarted."
files:
  - path: src/acheron/shell/job_events.py
    lines: 24-53
  - path: src/acheron/shell/orchestrator.py
    lines: 857-865
related: []
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
---
```

**Resolution.** Terminal event history is bounded by the broker's `max_terminal_jobs` limit and oldest entries are evicted. `tests/shell/test_job_events.py` covers the bounded terminal registry and eviction behavior, so the original indefinite-retention concern is no longer valid.
