---
theme: OPS
last_updated_date: 2026-07-28
version: 2
---

# OPS

**Grade**: D (7 high + 17 medium-severity open stories)
**Calibration target**: an operator should be able to submit, monitor, debug, and recover a job without `docker logs`.

## OPS-001 — Dashboard renders only three read-only tables

```yaml
---
id: OPS-001
title: Dashboard renders only three read-only tables; clicking a job row does nothing; no Last-error column
status: open
severity: high
effort: M
discovered_via: [code-review, on-call]
user_facing_surface: dashboard
silent: false
journey_stage: t1
user_journey: "Operator opens the dashboard during a 2-hour translation run, sees a row with a FAILED job, clicks the row, expects a detail page with start time, end time, error string, and at least one output link; gets nothing — the click does not navigate."
files:
  - path: dashboard/app.py
    lines: 62-82
  - path: dashboard/templates/partials/jobs.html
    lines: 1-29
related: [OBS-002]
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
incident_ref: "TBD-pagerduty"
---
```

**Issue.** `dashboard/app.py:62-82` exposes only `GET /partials/{jobs,workers,cost,status}`. `dashboard/templates/partials/jobs.html:1-29` renders a flat `<table>` with columns Job ID / Status / Progress / Cost / Duration. The `<tr>` has no link, no anchor, no `data-` attribute. The `JobResponse.errors` field is populated but the dashboard never reads it.

**Why it matters.** Every failure the operator hits is invisible at the level of detail needed to act. The fallback is `docker logs orchestrator` filtered by `job_id`.

**Recommendation.** (a) Add a `<tr>` link or HTMX click handler that navigates to `/partials/jobs/{id}` and renders a detail page. (b) Add a "Last error" column. (c) Plumb `source_type` / `source_language` / `target_language` / `asr_model` / `created_at` (tracked in OPS-004).

**Verification.** Submit a job, force a failure, open the dashboard, click the FAILED row, see a detail page with the error string and the timestamp.

## OPS-002 — CLI has no `watch` / `follow` mode

```yaml
---
id: OPS-002
title: "`acheron job submit` returns immediately; operator must wrap `watch -n 2 acheron job status` manually"
status: open
severity: high
effort: S
discovered_via: [user-feedback, code-review]
user_facing_surface: cli
silent: true
journey_stage: t1
user_journey: "Operator runs `acheron job submit book.epub --src en --dest es --follow`, expects a live progress bar that updates every 2s; gets `Job submitted: job-abc12345` and the prompt returns immediately."
files:
  - path: src/acheron/cli.py
    lines: 143-179
  - path: src/acheron/api_client.py
    lines: 75-82
related: [OPS-014]
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
feedback_ref: "TBD-pagerduty"
---
```

**Issue.** `src/acheron/cli.py:165-178` calls `_get_client().submit_job(...)`, prints the response, and returns. There is no `--follow` / `--watch` / `--tail` flag. `api_client.py:75-82` exposes `get_job()` but no streaming endpoint.

**Why it matters.** The operator's typical submission is a long-running translation or TTS job. Without a follow mode, the operator either context-switches to the dashboard or wraps `watch -n 2 acheron job status` in another shell.

**Recommendation.** Add `--follow` / `--watch` to `acheron job submit` and a new `acheron job watch <id>` command. Reuse `api_client.get_job`; poll every 2s with a `rich.live.Live` context that renders a progress bar, current step, ETA, and recent error.

**Verification.** `acheron job submit book.epub --src en --dest es --follow` shows a live progress bar. On COMPLETED, exit 0. On FAILED, exit 1 with the first error.

## OPS-003 — CLI surfaces Python exception class names; no remediation hints

```yaml
---
id: OPS-003
title: "CLI prints `Error 422: InvalidLanguagePathError: <message>` and exits; operator has no idea what languages are supported"
status: fixed
severity: high
effort: S
discovered_via: [code-review, user-feedback]
user_facing_surface: cli
silent: true
journey_stage: t1
user_journey: "Operator runs `acheron job submit book.epub --src en --dest xx` (typo), sees the error: 'no worker can translate en→xx; supported targets from en: es, fr, de; run `acheron capabilities --src en` to see the full list', exits 1."
files:
  - path: src/acheron/cli.py
    lines: 114-182
  - path: src/acheron/shell/api/routes/jobs.py
    lines: 58-90
related: [SEC-006, SEC-012, SEC-019]
fixed_in: [pending]
verified_in: []
last_verified_at:
  commit: pending
  date: "2026-07-28"
verified_by: ""
feedback_ref: "TBD-pagerduty"
---
```

**Issue.** The CLI's HTTP error path previously printed only the status and raw API detail, without remediation or a list of supported targets. The API also returned domain errors without their exception type, preventing the CLI from selecting a safe presentation. The same gap applied to `ChunkingTooLongForWorkerError`, `JobAlreadyRunningError`, and other `AcheronError` subclasses.

**Why it matters.** Every error that lacks a remediation hint forces a `docker logs` round-trip.

**Recommendation.** Define an `Error` presentation layer: a mapping from `AcheronError` subclass to a human-friendly message + a "next step" line. For `InvalidLanguagePathError`, call `api_client.get_capabilities(src=<src>)` and append "supported targets from {src}: {list}". For `ChunkingTooLongForWorkerError`, append the worker max_input_tokens.

**Verification.** Each error type renders a multi-line message with a remediation hint. Exit code is non-zero.

## OPS-004 — `JobResponse` carries no submission params or timestamps

```yaml
---
id: OPS-004
title: "`JobResponse` schema has no `source_type`, `source_language`, `target_language`, `asr_model`, `created_at`, `last_persisted_at`"
status: open
severity: high
effort: S
discovered_via: [code-review, on-call]
user_facing_surface: cli
silent: true
journey_stage: t1
user_journey: "Operator submits a job with `--src en --dest es --asr whisper-v3`, later runs `acheron job status job-xyz`, sees the response has no `source_language` / `target_language` / `asr_model` / `created_at` fields; cannot tell which ASR model was used or when the job started."
files:
  - path: src/acheron/core/schemas.py
    lines: 12-23
  - path: src/acheron/shell/api/routes/jobs.py
    lines: 96-108
related: [TYPE-005]
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
incident_ref: "TBD-pagerduty"
---
```

**Issue.** `src/acheron/core/schemas.py:12-23` defines `JobResponse` with only `job_id`, `status`, `plan_id`, `completed_steps`, `total_steps`, `total_cost`, `total_duration_seconds`, `total_cost_basis`, `errors`. The submission parameters are not echoed back; timestamps are not stored.

**Why it matters.** Operators cannot verify what was submitted, cannot filter by date, cannot tell "stuck 30 min" from "started 30 min ago".

**Recommendation.** Extend `JobResponse` with: `source_type`, `source_language`, `target_language`, `asr_model`, `executor_strategy`, `created_at`, `last_persisted_at`. Persist them in the job store. Add `acheron jobs --since <duration>` and `acheron jobs --before <iso>`.

**Verification.** Submit a job, then `acheron job status <id>` shows `source_language: en`, `target_language: es`, `asr_model: whisper-v3`, `created_at: 2026-07-24T...`.

## OPS-005 — Cost basis labels rendered without explanation

```yaml
---
id: OPS-005
title: "Dashboard's `MEASURED` / `CACHED` / `UNKNOWN` / `STATIC` cost-basis badges are rendered with no tooltip or legend"
status: open
severity: high
effort: S
discovered_via: [code-review, user-feedback]
user_facing_surface: dashboard
silent: true
journey_stage: t1
user_journey: "Operator hovers the `MEASURED` badge on a cost row, sees a tooltip: 'MEASURED: just asked RunPod for the rate. CACHED: last-known rate; GraphQL unavailable. UNKNOWN: no rate available. STATIC: fixed $/hr or zero (stub/local).'"
files:
  - path: dashboard/templates/partials/cost.html
    lines: 22-30
  - path: src/acheron/core/models.py
    lines: 68-74
related: [CORR-008, CORR-040, TYPE-005]
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
feedback_ref: "TBD-pagerduty"
---
```

**Issue.** `dashboard/templates/partials/cost.html:17-30` renders four basis labels with no tooltip. The `STATIC` basis covers two very different cases: a fixed `$/hr` AND `$0.00` (stub). An operator who sees `$0.00` and a `STATIC` badge reads it as "free."

**Why it matters.** Cost is a primary operator concern. The badge is a signal that something might be off, but a first-time operator has no way to learn the signal.

**Recommendation.** (a) Add a `?` icon next to each badge that opens a tooltip with the four-state legend. (b) Split `STATIC` into `STATIC` (fixed $/hr) and `ZERO` (stub/local). (c) Add a CLI: `acheron job cost <id> --explain`.

**Verification.** Hovering a `MEASURED` badge shows the four-state legend. A `$0.00` row shows `ZERO` with the "stub/local" note. `acheron job cost <id> --explain` prints the explanation.

## OPS-006 — `BOOTING` workers show no countdown

```yaml
---
id: OPS-006
title: "When a worker is in `BOOTING` (RunPod cold start), the dashboard shows a static badge with no countdown"
status: fixed
severity: high
effort: S
discovered_via: [user-feedback, code-review]
user_facing_surface: dashboard
silent: true
journey_stage: t1
user_journey: "Operator submits a job to a worker in `BOOTING`, sees the worker table column show `BOOTING (202s / 600s)` ticking every second, with a thin progress bar that reaches 100% at `_BOOTING_TIMEOUT_SECONDS` (10 min by default); on reaching HEALTHY, the bar fades and the column turns green."
files:
  - path: src/acheron/shell/registry.py
    lines: 14-32
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
  - path: src/acheron/shell/health.py
    lines: 27-29
  - path: src/acheron/shell/health.py
    lines: 174-226
  - path: src/acheron/core/schemas.py
    lines: 33-45
  - path: src/acheron/shell/api/routes/workers.py
    lines: 22-29
  - path: src/acheron/shell/api/routes/workers.py
    lines: 74-101
  - path: dashboard/booting_progress.py
    lines: 1-54
  - path: dashboard/app.py
    lines: 15-22
  - path: dashboard/templates/partials/workers.html
    lines: 13-20
  - path: dashboard/templates/index.html
    lines: 62-87
related: [CORR-012]
fixed_in: [828dd5a]
verified_in: []
last_verified_at: {}
verified_by: ""
feedback_ref: "TBD-pagerduty"
---
```

**Issue.** Worker BOOTING state previously had no persisted lifecycle timestamp or operator-facing elapsed time. The dashboard rendered only a static status badge, while the health monitor had no visible warning before the 600-second timeout.

**Why it matters.** Cold-start cost is real money. Without elapsed and timeout progress, an operator cannot distinguish a normal cold start from a worker that is approaching timeout.

**Recommendation.** Persist the BOOTING timestamp across store backends, expose clamped elapsed and timeout values in `WorkerResponse`, and render a browser-independent elapsed/timeout progress label with a one-second dashboard update. Keep the timestamp stable for a BOOTING lifecycle and clear it on HEALTHY, OFFLINE, or timeout transitions.

**Verification.** Focused store, health-monitor, worker-route, dashboard-helper, and dashboard-template tests cover timestamp persistence, backward-clock clamping, lifecycle transitions, 600-second timeout behavior, and one-second progress wiring. The implementation keeps job submission non-gating and leaves the status partial contract unchanged.

## OPS-007 — Dashboard status badge reflects worker-fleet readiness

```yaml
---
id: OPS-007
title: "Dashboard status badge reflects worker-fleet readiness"
status: fixed
severity: high
effort: S
discovered_via: [code-review, on-call]
user_facing_surface: dashboard
silent: true
journey_stage: t1
user_journey: "Operator opens the dashboard and sees a status badge reflecting the registered ASR, TRANSLATION, and TTS service-worker fleet. An empty or unhealthy fleet is yellow and reads 'Waiting'; green 'Ready' requires at least one healthy service worker and every registered service worker to be healthy. The badge shows deterministic per-type counts, and a dashboard fetch failure is red and reads 'Disconnected'. Submission gating is not part of OPS-007."
files:
  - path: src/acheron/shell/api/routes/partials.py
    lines: 1-46
  - path: dashboard/app.py
    lines: 37-46
  - path: dashboard/templates/index.html
    lines: 29-32
  - path: tests/shell/api/test_partials.py
    lines: 86-192
  - path: dashboard/tests/test_dashboard.py
    lines: 78-88
  - path: dashboard/tests/test_dashboard.py
    lines: 242-268
  - path: tests/first_run/test_3_success_criteria.py
    lines: 6-15
related: []
fixed_in: [dab052d]
verified_in: []
last_verified_at: {}
verified_by: ""
incident_ref: "TBD-pagerduty"
---
```

**Issue.** The dashboard status badge previously represented an HTTP 200 from `/partials/status` as "Connected" without checking worker-fleet readiness. The implementation now keeps the endpoint as an HTML fragment, counts only registered ASR, TRANSLATION, and TTS workers, and renders deterministic per-type healthy/total counts. An empty or unhealthy service fleet is yellow, while dashboard fetch failure remains red `Disconnected`.

**Why it matters.** The status badge is the operator's pre-flight signal: green means every registered service worker is healthy and at least one service worker is available, yellow means the fleet is empty or not fully healthy, and red means the dashboard cannot fetch its HTML fragment.

**Recommendation.** Keep `/partials/status` as an `HTMLResponse` and derive readiness from registered service workers. Render green `Ready` only when at least one ASR, TRANSLATION, or TTS worker is healthy and every registered service worker is healthy; otherwise render yellow `Waiting`, including the exact empty output `Waiting for workers (0/0 service workers healthy)`. Preserve deterministic per-type counts, the dashboard proxy's unchanged fragment forwarding, and its red `Disconnected` fallback. Do not gate job submission on this badge; submission gating is outside OPS-007.

**Verification.** Focused partial-route tests cover empty, ASR-only, TRANSLATION-only, TTS-only, mixed, partially healthy, and built-in-worker fleets, including the exact empty text and deterministic per-type counts. Dashboard tests cover unchanged HTML-fragment forwarding, red `Disconnected` on fetch failure, and the yellow status style; first-run coverage accepts the empty or per-type readiness fragments. No submission-gating behavior is changed or verified as part of OPS-007.

## OPS-008 — No `acheron job cancel` — operator can submit but cannot abort

```yaml
---
id: OPS-008
title: "No `acheron job cancel` — operator can submit but cannot abort a running job"
status: open
severity: high
effort: M
discovered_via: [user-feedback, code-review]
user_facing_surface: cli
silent: true
journey_stage: t1
user_journey: "Operator runs `acheron job submit book.epub --src en --dest es`, 30 seconds later realizes the target language is wrong, runs `acheron job cancel job-abc12345`, expects the job to abort with its partial state persisted and exit 0; gets `Error: No such command 'cancel'`."
files:
  - path: src/acheron/cli.py
    lines: 138-227
  - path: src/acheron/api_client.py
    lines: 50-91
related: [OPS-020, OPS-021]
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
feedback_ref: "TBD-pagerduty"
---
```

**Issue.** `src/acheron/cli.py:138-227` defines `@job.group()` with `submit`, `status`, and `resume` only. `api_client.py:50-91` exposes `submit_job`, `get_job`, and `resume_job` — no `cancel_job`. The orchestrator has a drain path but no public single-job cancel.

**Why it matters.** Submitting then realizing a mistake is the most common operator recovery flow. Today the operator must either wait or kill the orchestrator.

**Recommendation.** Add `acheron job cancel <id>` and a `POST /jobs/{id}/cancel` route. The route must mark the job `FAILED` (with a "cancelled by operator" reason), persist the partial `PlanResult`, and let the in-flight step's `asyncio.CancelledError` path record its current metrics.

**Verification.** `acheron job cancel job-abc` exits 0; `job status` shows `FAILED` with `errors[0] == "cancelled by operator"`.

## OPS-009 — No `acheron job retry` with edited parameters

```yaml
---
id: OPS-009
title: "No `acheron job retry` with edited parameters — `resume` re-runs the same plan"
status: open
severity: medium
effort: M
discovered_via: [user-feedback, code-review]
user_facing_surface: cli
silent: true
journey_stage: t1
user_journey: "Operator runs `acheron job submit book.epub --src en --dest es --asr whisper-v3`, the job fails because the audio is shorter than whisper-v3's minimum; operator wants to retry with `--asr whisper-tiny`, runs `acheron job retry job-abc --asr whisper-tiny`, expects a new execution with the corrected ASR model."
files:
  - path: src/acheron/cli.py
    lines: 219-227
  - path: src/acheron/shell/api/schemas.py
    lines: 22-30
related: [OPS-008]
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
feedback_ref: "TBD-pagerduty"
---
```

**Issue.** `src/acheron/cli.py:219-227`'s `resume` re-runs the original `TrackedJob.request` verbatim. `SubmitJobRequest` has no override mechanism.

**Why it matters.** A retry is a new submission, not a resume. Operators who learn the wrong ASR model or the wrong chunk size must submit a new job and lose the job_id.

**Recommendation.** Add `acheron job retry <id> --src ... --dest ... --asr ...` that submits a fresh job (new `job_id`) but links the new job to the old one (`retries_from: <old-id>`). Optionally accept `--reuse-cache`.

**Verification.** `acheron job retry job-abc --asr whisper-tiny` returns a new `job_id` whose `retries_from` references the original.

## OPS-010 — `job status` shows `completed` but no output path

```yaml
---
id: OPS-010
title: "`acheron job status` shows `completed` but no output path — operator cannot find the audiobook"
status: open
severity: high
effort: S
discovered_via: [user-feedback, code-review]
user_facing_surface: cli
silent: true
journey_stage: t1
user_journey: "Operator runs `acheron job status job-abc12345`, sees `Status: completed`, expects a line `Output: <data_dir>/job-abc12345/output.m4b` so they can copy it; gets only the status badge and counters."
files:
  - path: src/acheron/cli.py
    lines: 202-217
  - path: src/acheron/core/schemas.py
    lines: 12-23
related: [OPS-028, OPS-001]
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
feedback_ref: "TBD-pagerduty"
---
```

**Issue.** `src/acheron/cli.py:202-217` prints `Job`, `Status`, `Plan`, `Steps`. It never prints outputs. `JobResponse` has no `outputs` field.

**Why it matters.** The deliverable artifact is the entire point of the pipeline.

**Recommendation.** Add `outputs: list[OutputSummary]` to `JobResponse` (path, filename, size_bytes, content_type).

**Verification.** Submit and complete a job; `acheron job status <id>` shows `Output: /data/job-abc/output.m4b (12.3 MB, audio/mp4)`.

## OPS-011 — `plan_id` is exposed but no `acheron job plan` command

```yaml
---
id: OPS-011
title: "`JobResponse.plan_id` is exposed but there is no `acheron job plan` command to inspect it"
status: verified
severity: medium
effort: S
discovered_via: [code-review, user-feedback]
user_facing_surface: cli
silent: true
journey_stage: t1
user_journey: "Operator runs `acheron job status job-abc`, sees `Plan: plan-9c0a1b`, wants to know how many steps are in the plan and which workers were assigned, runs `acheron job plan plan-9c0a1b`, expects a table of step_id / worker_type / depends_on / status (no duration — duration tracking is explicitly out of scope for this plan preview surface; the user approved the reduced structure-only contract)."
files:
  - path: src/acheron/core/schemas.py
    lines: 92-130
  - path: src/acheron/shell/api/routes/plans.py
    lines: 14-30
  - path: src/acheron/shell/orchestrator.py
    lines: 438-440
  - path: src/acheron/api_client.py
    lines: 124-131
  - path: src/acheron/cli.py
    lines: 208-225
  - path: src/acheron/cli.py
    lines: 376-391
  - path: tests/core/test_schemas.py
    lines: 195-221
  - path: tests/shell/api/test_plans.py
    lines: 1-93
  - path: tests/test_api_client.py
    lines: 506-527
  - path: tests/shell/test_cli.py
    lines: 183-249
related: [OPS-001, OPS-013]
fixed_in: [07a7ba5, 57d0bc2, f01dabb, 68d7f29]
verified_in: [6dc6353f1b9f8ed85d6d8a64bc1a3672cbb3da72]
last_verified_at:
  commit: 6dc6353f1b9f8ed85d6d8a64bc1a3672cbb3da72
  date: "2026-07-29"
verified_by: "harness:phase-4b-journey+pytest"
feedback_ref: "TBD-pagerduty"
---
```

**Issue.** `PlanStepResponse` (`src/acheron/core/schemas.py:92-98`) now exposes only the public fields a plan consumer needs: `step_id`, `worker_type`, `depends_on`, `status`; the internal `PlanStep.payload` (e.g. `source_path`) is never carried over. `PlanResponse.from_plan` (`src/acheron/core/schemas.py:113-130`) is the single mapper from the internal `Plan` dataclass to the wire shape, so a future refactor that adds new internal fields cannot accidentally leak them. `GET /plans/{plan_id}` (`src/acheron/shell/api/routes/plans.py:14-30`) is implemented behind `OrchestratorDep` (no registration-token dependency), turns `CacheMissError` into HTTP 404 with `Plan not found: <id>`, and turns `CacheCorruptedError` into a generic HTTP 500 with no on-disk detail. `PlanCache._plan_file` (`src/acheron/shell/cache.py:_plan_file` via `_PLAN_ID_RE = re.compile(r"\Aplan-[0-9a-f]+\Z")`) rejects any non-conforming ID before the filesystem is touched, so traversal-style IDs such as `../escaped` and `plan-../escape` both map to `CacheMissError` and become 404. `Orchestrator.get_plan` (`src/acheron/shell/orchestrator.py:438-440`) is a thin threaded wrapper over the cache. `AcheronClient.get_plan` (`src/acheron/api_client.py:124-131`) calls `GET /plans/{plan_id}` and returns the parsed `PlanResponse`. `acheron job plan` (`src/acheron/cli.py:376-391`) is the new top-level command: it takes a positional `PLAN_ID` or `--job JOB_ID`, and a `click.UsageError("provide exactly one plan ID or --job JOB_ID")` is raised when both are given or neither is given; `--job` resolves via `get_job` and surfaces a friendly `Job {id} has no plan ID` followed by `SystemExit(1)` if the job has none. `_print_plan` (`src/acheron/cli.py:208-225`) renders a `Step / Worker type / Depends on / Status` table from the public `PlanResponse` and never prints `step.payload` or any absolute on-disk path. `app.include_router(plans.router, prefix="/plans", tags=["plans"])` (`src/acheron/shell/api/app.py`) is the only wiring change to the FastAPI app.

**Why it matters.** When a job fails, the operator's first question is "which step?". With a typed, public-only `PlanResponse` plus a CLI lookup that does not require a bearer token (matches the existing `GET /jobs/{id}` posture — both are read-only diagnostic surfaces), the operator can read the plan from any machine that can reach the orchestrator without needing the registration secret. The `_PLAN_ID_RE` guard makes the lookup safe to expose even though it is read-only.

**Recommendation.** Keep the public response shape, the read-only posture on `GET /plans/{plan_id}`, and the regex-enforced plan ID contract. Preserve the "exactly one selector" usage error and the friendly "no plan ID" remediation on the CLI. Do not include step payloads in the wire shape; do not require a bearer token for `GET /plans/{plan_id}`; do not weaken the regex or accept any other plan ID format.

**Verification.** A live orchestrator session captured in `.superpowers/sdd/2026-07-29-phase-4b-plan-preview/ops-011-016-journey.txt` shows: (1) `acheron job plan plan-a312fc67` renders a 5-step table (extract / chunk / translate / synthesize / package); (2) `acheron job plan --job job-eebb2d80` resolves the plan ID via `GET /jobs/<id>` and renders the same table; (3) `acheron job plan` with no selector and `acheron job plan <plan> --job <job>` with both selectors both exit non-zero with `Error: provide exactly one plan ID or --job JOB_ID`; (4) `GET /plans/..%2Fescaped`, `GET /plans/..%2Fplan-X`, and `GET /plans/plan-..%2Fescape` all return HTTP 404 (regex guard); (5) the `payload` key is absent from every step in the live `GET /plans/<id>` response. Focused tests: `tests/shell/api/test_plans.py` covers the public structure, traversal rejection, and 500-without-leaking-details on corrupted cache; `tests/test_api_client.py:506-527` round-trips `get_plan`; `tests/shell/test_cli.py:183-249` covers the positional lookup, the `--job` resolution, and the exactly-one-selector usage error; `tests/core/test_schemas.py` covers `PlanResponse.from_plan`.

## OPS-012 — `acheron jobs` has no time-window filter, no status filter, no archive/delete

```yaml
---
id: OPS-012
title: "`acheron jobs` has no time-window filter, no status filter beyond binary --active/--completed, no archive/delete"
status: open
severity: medium
effort: S
discovered_via: [user-feedback, code-review]
user_facing_surface: cli
silent: false
journey_stage: t1
user_journey: "Operator runs `acheron jobs` after a month of use, sees 200 rows, wants `acheron jobs --since 24h` to see only today's jobs, then `acheron jobs --archive job-old1 job-old2` to prune the table."
files:
  - path: src/acheron/cli.py
    lines: 229-250
related: [OPS-031, OPS-004]
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
feedback_ref: "TBD-pagerduty"
---
```

**Issue.** `src/acheron/cli.py:229-250` defines `list_jobs` with only `--active` and `--completed`; no `--since`, `--status`, `--until`, `--label`, or archive subcommand.

**Why it matters.** A growing unbounded list means the operator scrolls past stale jobs.

**Recommendation.** Add `--since <duration>` and `--before <iso>` to `acheron jobs`. Add `--status <state>`. Add `acheron job archive <id>...` and `acheron jobs --prune --older-than 30d --status completed`.

**Verification.** `acheron jobs --since 1h` returns only the recent jobs. `acheron job archive job-abc` removes the row from the active list.

## OPS-013 — Failed step's `worker_id` is invisible in `JobResponse.errors`

```yaml
---
id: OPS-013
title: "Failed step's `worker_id` is invisible; `JobResponse.errors` is a flat `list[str]` with no per-step attribution"
status: open
severity: high
effort: M
discovered_via: [user-feedback, code-review]
user_facing_surface: cli
silent: true
journey_stage: t1
user_journey: "Operator runs a 5-step job, step 3 fails, runs `acheron job status job-abc --verbose`, sees `Error: chunking failed: input too long (50000 chars)`, can't tell which worker raised it; expects each error line to include `[step=3, worker_type=chunking, worker_id=chunking-local]`."
files:
  - path: src/acheron/core/schemas.py
    lines: 12-23
  - path: src/acheron/shell/api/routes/jobs.py
    lines: 96-108
related: [OPS-001, OPS-011, OPS-023]
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
feedback_ref: "TBD-pagerduty"
---
```

**Issue.** `src/acheron/core/schemas.py:12-23` defines `errors: list[str]`. The original `JobResult.error` and the per-step `step_id` / `worker_id` are dropped at `_record_step_progress` time. The `PlanStep.step_id` and the dispatcher's `worker_id` are never attached to the error.

**Why it matters.** When a multi-step job fails, the operator's first triage is "which step, which worker?"

**Recommendation.** Replace `errors: list[str]` with `errors: list[StepError]` where `StepError` carries `step_id`, `worker_type`, `worker_id`, `message`, `timestamp`.

**Verification.** Force a step failure. `acheron job status <id> --verbose` shows `Error [step=2, worker_type=chunking, worker_id=chunking-local]: input too long`.

## OPS-014 — No `acheron job tail` / `acheron job log` — operator cannot see what the worker is doing

```yaml
---
id: OPS-014
title: "No `acheron job tail` / `acheron job log` — operator cannot see what the worker is doing right now"
status: open
severity: medium
effort: L
discovered_via: [user-feedback]
user_facing_surface: cli
silent: true
journey_stage: t1
user_journey: "Operator runs a long TTS job, runs `acheron job tail job-abc` to follow the worker's stdout, expects a live stream of per-step progress (`[step 3/10] tts-1: synthesizing chunk 47/100`)."
files:
  - path: src/acheron/cli.py
    lines: 1-292
  - path: src/acheron/api_client.py
    lines: 1-136
related: [OPS-002, OPS-001]
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
feedback_ref: "TBD-pagerduty"
---
```

**Issue.** There is no per-job log stream. The orchestrator records per-step progress at `_record_step_progress` but the progress is only in-memory.

**Why it matters.** During a 30-min TTS run, the operator needs a terminal-resident view of "where is the work right now?"

**Recommendation.** Add `GET /jobs/{id}/logs?follow=true` that streams the in-memory step progress as newline-delimited JSON. `acheron job tail <id>` is a thin wrapper using httpx streaming.

**Verification.** Submit a long TTS job, `acheron job tail <id>` shows one line every ~2s with the current step and chunk count.

## OPS-015 — `capabilities` only filters by language pair; no per-type view

```yaml
---
id: OPS-015
title: "`acheron capabilities` only filters by language pair; cannot ask \"what ASR models are registered?\" or \"what TTS voices?\""
status: fixed
severity: medium
effort: S
discovered_via: [user-feedback, code-review]
user_facing_surface: cli
silent: true
journey_stage: t1
user_journey: "Operator runs `acheron capabilities --tts` expecting a list of registered TTS workers with their `model_source` and `metadata.voice`."
files:
  - path: src/acheron/shell/api/routes/capabilities.py
    lines: 18-81
  - path: src/acheron/core/schemas.py
    lines: 64-85
  - path: src/acheron/api_client.py
    lines: 184-191
  - path: src/acheron/cli.py
    lines: 395-437
  - path: tests/shell/api/test_capabilities.py
    lines: 29-126
  - path: tests/test_api_client.py
    lines: 99-188
  - path: tests/shell/test_cli.py
    lines: 421-499
  - path: README.md
    lines: 60-68
related: [OPS-024, OPS-028]
fixed_in: [007a0427498ebb921f9273a4bdb9b3f0a66eee15]
verified_in: []
last_verified_at: {}
verified_by: ""
feedback_ref: "TBD-pagerduty"
---
```

**Issue.** The capabilities surface now supports typed inventory queries in addition to language-pair queries. `GET /capabilities?type=tts|asr|translation` returns a `workers` list with each worker's ID, type, `model_source`, and metadata; the server sorts workers by ID and leaves `language_pairs` empty in typed mode. Pair mode remains unchanged and returns `workers: []`.

**Why it matters.** Operators can discover the deployed ASR models and TTS voices before choosing a submission configuration.

**Recommendation.** Keep `acheron capabilities --type tts|asr|translation` backed by the typed endpoint. Render the selected workers as a Worker ID / Model / Voice table, using `-` for missing or non-string voice metadata, and reject combinations with `--src` or `--dest`.

**Verification.** With the orchestrator running and TTS workers registered, run `acheron capabilities --type tts`; the command exits 0 and displays a `TTS Workers` table with each worker's ID, model, and voice (or `-` when no voice is advertised), giving the operator the inventory needed to choose a worker.

## OPS-016 — `acheron job submit` has no `--dry-run`

```yaml
---
id: OPS-016
title: "`acheron job submit` has no `--dry-run` — operator cannot preflight the plan before committing"
status: verified
severity: medium
effort: M
discovered_via: [user-feedback, code-review]
user_facing_surface: cli
silent: true
journey_stage: t1
user_journey: "Operator runs `acheron job submit book.epub --src en --dest es --dry-run`, expects a printed plan with steps and worker assignment, and exits 0 without persisting (no cost estimate — cost estimation is explicitly a non-goal for the preview; the user approved the reduced non-persisting preview contract)."
files:
  - path: src/acheron/cli.py
    lines: 270-312
  - path: src/acheron/api_client.py
    lines: 99-122
  - path: src/acheron/shell/api/routes/jobs.py
    lines: 73-150
  - path: src/acheron/shell/orchestrator.py
    lines: 405-435
  - path: tests/shell/test_cli.py
    lines: 251-277
  - path: tests/shell/test_orchestrator.py
    lines: 210-230
  - path: tests/shell/api/test_jobs.py
    lines: 1034-1094
  - path: tests/test_api_client.py
    lines: 473-503
related: [OPS-011, OPS-019, OPS-025]
fixed_in: [84aa30c, 57d0bc2, f01dabb, 68d7f29]
verified_in: [6dc6353f1b9f8ed85d6d8a64bc1a3672cbb3da72]
last_verified_at:
  commit: 6dc6353f1b9f8ed85d6d8a64bc1a3672cbb3da72
  date: "2026-07-29"
verified_by: "harness:phase-4b-journey+pytest"
feedback_ref: "TBD-pagerduty"
---
```

**Issue.** `Orchestrator.preview_job` (`src/acheron/shell/orchestrator.py:430-435`) is a thin wrapper over the shared `_compile_plan` (`src/acheron/shell/orchestrator.py:405-426`) that omits the `job_id` argument so `compile_plan` mints a throwaway `job_id` for the in-memory plan. It does not call `self._cache.save_plan`, `await self._job_store.put`, `self._track_execution_task`, or `self._invalidate_handler_cache` — the only side effect is the return value. The new `POST /jobs:preview` route (`src/acheron/shell/api/routes/jobs.py:135-150`) reuses `_build_job_request` (`src/acheron/shell/api/routes/jobs.py:73-132`) so the exact same preflight gates preview and submit (source-type/`asr_model`/strategy validation, the data-dir source-path resolution) — operators see the same 422 an actual submit would experience. The route is registered behind `RegistrationTokenDep` (`src/acheron/shell/api/routes/jobs.py:139`) so a `POST /jobs:preview` request without a matching bearer is rejected with 401 when `ACHERON_REGISTRATION_TOKEN` is set, matching the existing `POST /jobs` posture. The `PlanResponse.from_plan` mapper (`src/acheron/core/schemas.py:113-130`) is the only public shape the preview route returns. `AcheronClient.preview_job` (`src/acheron/api_client.py:99-122`) forwards the existing submit payload to `POST /jobs:preview` and uses the same mutation/bearer headers as `submit_job`, so a CLI that has a token for submit has a token for preview. The CLI's `submit` (`src/acheron/cli.py:270-312`) now has a `@click.option("--dry-run", is_flag=True, help="Preview the plan without submitting a job")` and, when set, branches immediately after the existing `_run(... upload_input(file) ...)` call to call `preview_job` and then `_print_plan(preview, dry_run=True)`. The branch ends with `return` so the existing `_run(... submit_job ...)` block is never entered. `_print_plan` (`src/acheron/cli.py:208-225`) sets the title to `Plan preview` and appends `Dry run complete; no job submitted.` when `dry_run=True`. The normal submit path is byte-for-byte unchanged: `--dry-run=False` flows through the same `submit_job` call as before, which still calls `self._cache.save_plan(plan)` and `await self._job_store.put(tracked)` (`src/acheron/shell/orchestrator.py:384-401`).

**Why it matters.** Plan compilation is fast and produces all the data the operator needs to decide whether to commit. Without a dry-run, the operator pays the cost of a full submission (and the cold-start cost of a TTS worker, OPS-019) just to learn "actually I mistyped the source language." The new endpoint also lets the dashboard and other operator tooling render a "what would this look like" view without needing a placeholder job row to clean up afterwards.

**Recommendation.** Keep the dry-run branch in front of the submit branch in the CLI so the `submit_job` call site is unreachable when `--dry-run` is set. Reuse `_build_job_request` for both `POST /jobs` and `POST /jobs:preview` so the two endpoints cannot drift in their preflight behaviour. Keep `preview_job` free of any persistence or scheduling side effect, and keep the `RegistrationTokenDep` on the preview route (a preview is still a mutating request from the cluster's perspective — it consumes the planner). Do not weaken the `PlanResponse` shape to include step payloads; the dry-run is a UI surface, not a data export.

**Verification.** A live orchestrator session captured in `.superpowers/sdd/2026-07-29-phase-4b-plan-preview/ops-011-016-journey.txt` shows: (1) `acheron job submit /tmp/book.epub --src en --dest es --dry-run` prints the 5-step plan with title `Plan preview` and the line `Dry run complete; no job submitted.`, then exits 0; (2) `acheron jobs` taken after the dry run is identical to `acheron jobs` taken before it — only the single pre-existing `job-eebb2d80` row is present, so the dry run created no `job-*` row; (3) with a token set and no bearer, `POST /jobs:preview` is rejected with HTTP 401 `Missing Authorization header` (preview shares the auth posture of submit); (4) the returned `plan_id` and `job_id` in the dry-run output match the response from the live `POST /jobs:preview` endpoint, confirming the CLI and the route agree on the shape. Focused tests: `tests/shell/test_cli.py:251-277` (`test_submit_dry_run_previews_without_submitting`) does not mock `POST /jobs`, so any spurious call to it would raise `RequestNotConfigured`; `tests/shell/test_orchestrator.py:210-230` (`test_preview_job_compiles_without_persistence`) asserts `jobs.list_all() == ()` and `cache.plan_exists(plan.plan_id) is False` after a preview; `tests/shell/api/test_jobs.py:1034-1094` covers the preview route's preflight reuse and the no-payload wire shape; `tests/test_api_client.py:473-503` asserts the preview client's payload and bearer header.

## OPS-017 — Jobs have no human-readable name

```yaml
---
id: OPS-017
title: "Jobs have no human-readable name; `job-abc12345` is the only identity"
status: open
severity: low
effort: S
discovered_via: [user-feedback]
user_facing_surface: cli
silent: false
journey_stage: t1
user_journey: "Operator submits 5 jobs for 'Project Atlas' chapters 1-5, all 5 rows in `acheron jobs` look the same except the job_id; operator wants `acheron job submit book.epub --src en --dest es --label 'atlas-ch1'` and `acheron jobs --label atlas-*` to filter."
files:
  - path: src/acheron/cli.py
    lines: 143-179
  - path: src/acheron/shell/api/schemas.py
    lines: 22-30
related: [OPS-012]
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
feedback_ref: "TBD-pagerduty"
---
```

**Issue.** `src/acheron/cli.py:143-179` and `SubmitJobRequest` define no `label` field. The `job_id` is `job-{uuid.uuid4().hex[:8]}`, which is unmemorable.

**Why it matters.** After 20 submissions the operator is grep'ing their shell history for which job_id was which project.

**Recommendation.** Add `label: str | None` to `SubmitJobRequest`. Persist it in `TrackedJob`. Add `--label` to `submit` and `--label <glob>` to `jobs`.

**Verification.** `acheron job submit … --label atlas-ch1` returns a job whose `job status` shows `Label: atlas-ch1`.

## OPS-018 — `submit --asr <model>` is silently dropped when `source_type=epub`

```yaml
---
id: OPS-018
title: "`submit --asr <model>` is silently dropped when source_type=epub"
status: fixed
severity: medium
effort: S
discovered_via: [code-review, user-feedback]
user_facing_surface: cli
silent: true
journey_stage: t1
user_journey: "Operator runs `acheron job submit book.epub --src en --dest es --asr whisper-v3` (typo: used --asr for an EPUB), expects a warning 'ASR model ignored for epub input'; gets 'Job submitted: job-abc12345' with no warning, and the model is silently dropped."
files:
  - path: src/acheron/cli.py
    lines: 242-286
  - path: src/acheron/shell/api/routes/jobs.py
    lines: 69-95
  - path: tests/shell/api/test_jobs.py
    lines: 585-611
related: [OPS-029, OPS-003]
fixed_in: [007a0427498ebb921f9273a4bdb9b3f0a66eee15]
verified_in: []
last_verified_at: {}
verified_by: ""
feedback_ref: "TBD-pagerduty"
---
```

**Issue.** EPUB submissions now reject a supplied `asr_model`, including blank or whitespace values, before source-path resolution or `orch.submit_job()`, returning HTTP 422 with `asr_model is only valid for source_type='audio'`. The CLI still uploads first, then forwards the server-relative upload path and the requested ASR model to `/jobs`, so the invalid EPUB request is no longer accepted as if the model applied.

**Why it matters.** The operator receives an explicit validation error instead of a successful submission that silently discards the requested ASR model.

**Recommendation.** Keep the source-type guard at the API boundary and preserve the exact reason in the 422 detail. Do not accept or silently drop `asr_model` for EPUB requests.

**Verification.** Starting with a valid EPUB and a running orchestrator, run `acheron job submit book.epub --src en --dest es --asr whisper-v3`; the command exits 1, prints `asr_model is only valid for source_type='audio'`, and returns no job ID, so the operator sees that the ASR choice does not apply to EPUB input instead of a silent submission.

## OPS-019 — Submitting while fleet is `BOOTING` — no "this will queue 30-90s" hint

```yaml
---
id: OPS-019
title: "Submitting while fleet is `BOOTING` — CLI says \"Job submitted\" with no \"this will queue 30-90s\" hint"
status: fixed
severity: medium
effort: S
discovered_via: [user-feedback, code-review]
user_facing_surface: cli
silent: true
journey_stage: t1
user_journey: "Operator submits a job while all TTS workers are in `BOOTING`, sees `Job submitted: job-abc12345` and a `RUNNING` status badge, expects the response or the status badge to read 'queued: TTS workers BOOTING (202s elapsed); cold-start typical 30-90s'."
files:
  - path: src/acheron/shell/api/routes/jobs.py
    lines: 68-73
  - path: src/acheron/shell/api/routes/jobs.py
    lines: 111-133
  - path: src/acheron/core/schemas.py
    lines: 12-24
  - path: src/acheron/cli.py
    lines: 272-277
related: [OPS-007, OPS-006]
fixed_in: [b2bad9c]
verified_in: []
last_verified_at: {}
verified_by: ""
feedback_ref: "TBD-pagerduty"
---
```

**Issue.** After `POST /jobs` accepts a job, the API now performs best-effort inspection of the registered worker snapshot and adds a deterministic warning for BOOTING TTS workers. The warning helper clamps elapsed time to zero, sorts all affected worker IDs, and does not gate the accepted job.

**Why it matters.** Cold-start cost is real money. The operator who submits to a cold fleet and sees no signal for 60s panics and submits again, doubling the cost.

**Recommendation.** Return informational `warnings: list[str]` values on the accepted `JobResponse` and render them in yellow in the CLI while preserving HTTP 201 and CLI success semantics when inspection fails.

**Verification.** Focused API-schema, job-route, API-client, and CLI tests cover sorted multi-worker warnings, elapsed-time clamping, warning-inspection failure, wire round-trip, yellow CLI output, and non-gating accepted-job behavior. A BOOTING TTS fleet returns a warning while submission remains HTTP 201.

## OPS-020 — `resume` on a running job — error has no "use `cancel`" hint

```yaml
---
id: OPS-020
title: "`acheron job resume` on a running job — error string has no \"use `cancel`\" remediation hint"
status: open
severity: medium
effort: S
discovered_via: [user-feedback, code-review]
user_facing_surface: cli
silent: true
journey_stage: t1
user_journey: "Operator runs `acheron job resume job-abc` while a stale execution is still running, gets `Error 400: Job job-abc is already running`, has no idea how to cancel."
files:
  - path: src/acheron/cli.py
    lines: 92-99
  - path: src/acheron/shell/orchestrator.py
    lines: 707-710
related: [OPS-008, OPS-003]
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
feedback_ref: "TBD-pagerduty"
---
```

**Issue.** `JobAlreadyRunningError`'s message is "Job is already running". The CLI prints the error and exits; the operator has no idea how to stop it.

**Why it matters.** The operator who hits this error has exactly one question: "how do I stop the running one?"

**Recommendation.** Add a `remediation` field to the wire shape. The CLI renders the next-command as a copy-pasteable line.

**Verification.** `acheron job resume <id>` while running exits 1 with `Error 400: Job job-abc is already running` and a follow-up line `Try: acheron job cancel job-abc`.

## OPS-021 — `resume` on a no-plan job — error has no "re-submit" hint

```yaml
---
id: OPS-021
title: "`resume` on a job with no saved plan — error string doesn't tell the operator what to do"
status: open
severity: medium
effort: S
discovered_via: [code-review, on-call]
user_facing_surface: cli
silent: true
journey_stage: t1
user_journey: "Operator runs `acheron job resume job-old` on a job whose plan was wiped (after a data-dir cleanup), gets `Error 422: Job job-old has no saved plan to resume`; expects the message to read '… no saved plan to resume; the job must be re-submitted with `acheron job submit`'."
files:
  - path: src/acheron/cli.py
    lines: 219-227
  - path: src/acheron/shell/orchestrator.py
    lines: 714-716
related: [OPS-008, OPS-009, OPS-003]
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
incident_ref: "TBD-pagerduty"
---
```

**Issue.** `orchestrator.py:714-716` raises a bare `AcheronError("Job has no saved plan to resume")`.

**Why it matters.** A missing plan is almost always a data-dir cleanup artifact. The right next action is "re-submit," not "dig through the job store."

**Recommendation.** Define `NoPlanToResumeError(AcheronError)` with a structured `remediation` field. The CLI prints the remediation.

**Verification.** Wipe `<data_dir>/plans/plan-old`, `acheron job resume job-old` exits 1 with `Error 422: Job job-old has no saved plan to resume` and `Try: acheron job submit <source> --src ... --dest ...`.

## OPS-022 — 4xx/5xx from wrong base URL doesn't echo the attempted URL

```yaml
---
id: OPS-022
title: 4xx/5xx from the wrong base URL doesn't echo the attempted URL — operator can't tell they hit a different host
status: open
severity: medium
effort: S
discovered_via: [user-feedback, on-call]
user_facing_surface: cli
silent: true
journey_stage: t1
user_journey: "Operator's `ACHERON_URL` env var points to a stale deploy, `acheron job status job-abc` returns `Error 404: Not Found` (from the wrong host), has no idea they hit the wrong orchestrator."
files:
  - path: src/acheron/cli.py
    lines: 92-99
  - path: src/acheron/api_client.py
    lines: 50-136
related: [OPS-003]
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
feedback_ref: "TBD-pagerduty"
incident_ref: "TBD-pagerduty"
---
```

**Issue.** `cli.py:92-99`'s `httpx.HTTPStatusError` handler prints `Error {status_code}: {detail}` and exits. The request URL is available on `exc.request.url` but is not echoed. The connect-error path (lines 82-91) does echo the URL; the status-error path does not.

**Why it matters.** "Is the job gone?" vs "did I hit the wrong host?" is the operator's first triage question.

**Recommendation.** In `cli.py:92-99`, append ` (from {exc.request.url})` to the error line.

**Verification.** With `ACHERON_URL=https://wrong.host`, `acheron job status job-abc` exits 1 with `Error 404: Not Found (from https://wrong.host/jobs/job-abc) — verify ACHERON_URL`.

## OPS-023 — Dashboard per-job detail lacks per-step worker attribution

```yaml
---
id: OPS-023
title: "Failed step's `worker_id` is invisible in dashboard's `partials/jobs.html` — only a flat error column will surface it"
status: open
severity: medium
effort: S
discovered_via: [user-feedback, code-review]
user_facing_surface: dashboard
silent: true
journey_stage: t1
user_journey: "Operator opens the dashboard during a failing run, sees a FAILED row, clicks the row, gets a per-job detail page (OPS-001 follow-through) where each failed step shows `[step=3, worker_type=tts, worker_id=tts-1] <error message>`."
files:
  - path: dashboard/templates/partials/jobs.html
    lines: 1-29
  - path: dashboard/app.py
    lines: 62-65
related: [OPS-001, OPS-013]
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
feedback_ref: "TBD-pagerduty"
---
```

**Issue.** `dashboard/templates/partials/jobs.html:1-29` has no error column and no drill-down link. The wire at `schemas.py:12-23` exposes only `errors: list[str]`.

**Why it matters.** The dashboard is the operator's daily surface. Without per-step worker attribution, the operator opens a 2nd terminal for `docker logs`.

**Recommendation.** When the schema is extended to `StepError` (OPS-013), render each error as a `<tr>` with `step_id / worker_id / message / timestamp`.

**Verification.** Force a step failure. The dashboard's per-job detail shows a table with `step_id / worker_id / message / timestamp`.

## OPS-024 — `capabilities --src xx` (typo) returns empty silently

```yaml
---
id: OPS-024
title: "`acheron capabilities --src xx` (typo) returns empty list silently — operator can't tell \"no workers\" from \"typo\""
status: fixed
severity: medium
effort: S
discovered_via: [user-feedback, code-review]
user_facing_surface: cli
silent: true
journey_stage: t1
user_journey: "Operator runs `acheron capabilities --src xx` (typo: should be `--src en`), sees 'No language pairs available.' and exits 0, can't tell if the typo was the problem or if `xx` is genuinely unsupported."
files:
  - path: src/acheron/shell/api/routes/capabilities.py
    lines: 52-75
  - path: src/acheron/cli.py
    lines: 426-437
  - path: tests/shell/api/test_capabilities.py
    lines: 90-117
  - path: tests/test_api_client.py
    lines: 168-190
  - path: tests/integration/test_worker_registration.py
    lines: 53-65
related: [OPS-015, OPS-003]
fixed_in: [007a0427498ebb921f9273a4bdb9b3f0a66eee15]
verified_in: []
last_verified_at: {}
verified_by: ""
feedback_ref: "TBD-pagerduty"
---
```

**Issue.** The capabilities route builds a sorted union of all registered workers' input and output languages. An unknown `src` or `dest` now returns HTTP 422 with the requested language and the supported source/target list; a known language pair with no bridging worker remains a successful empty `language_pairs` result.

**Why it matters.** The CLI no longer conflates a language typo with a valid filter that currently has no workers.

**Recommendation.** Keep the route's deterministic language validation and preserve the distinction between unknown-language errors and known-but-empty pair results. The CLI should surface the 422 detail and return a non-zero exit status.

**Verification.** With the orchestrator running and `xx` absent from the registered language set, run `acheron capabilities --src xx`; the command exits 1 and prints an HTTP 422 message naming `xx` and the supported source languages, giving the operator a visible typo signal instead of `No language pairs available.`

## OPS-025 — `source_path` is not validated at submit

```yaml
---
id: OPS-025
title: "`source_path` is not validated at submit — typo fails 30s in on the first extraction step"
status: fixed
severity: medium
effort: S
discovered_via: [user-feedback, code-review]
user_facing_surface: cli
silent: true
journey_stage: t1
user_journey: "Operator runs `acheron job submit bk.epub --src en --dest es` (typo: `bk.epub` doesn't exist), gets 'Job submitted: job-abc12345' and a `RUNNING` badge; the plan compiles, the first extraction step fails 30s later with `FileNotFoundError`."
files:
  - path: src/acheron/cli.py
    lines: 242-286
  - path: src/acheron/shell/api/routes/inputs.py
    lines: 15-52
  - path: src/acheron/shell/api/routes/jobs.py
    lines: 25-93
  - path: src/acheron/shell/input_store.py
    lines: 49-150
  - path: src/acheron/shell/local_handlers.py
    lines: 245-275
  - path: tests/shell/api/test_inputs.py
    lines: 41-212
  - path: tests/shell/api/test_jobs.py
    lines: 554-974
  - path: tests/shell/test_input_store.py
    lines: 20-303
  - path: README.md
    lines: "70"
related: [OPS-016, OPS-003]
fixed_in: [007a0427498ebb921f9273a4bdb9b3f0a66eee15, 8dce27f40763493b28bb6849d356db0b3e276fda]
verified_in: []
last_verified_at: {}
verified_by: ""
feedback_ref: "TBD-pagerduty"
---
```

**Issue.** The CLI uploads the local file to `/inputs` and submits the returned server-relative path. The job route resolves that path under the orchestrator data directory and rejects empty, absolute, traversal, missing, directory, and symlink-escape paths with HTTP 422 before calling `orch.submit_job()`. Valid paths are passed downstream as absolute paths, while the existing local extraction handler continues to resolve and enforce its allowlist.

**Why it matters.** A typo or unsafe path is rejected before plan compilation and execution, with a location-specific error instead of a delayed extraction failure.

**Recommendation.** Keep upload storage atomic and bounded, return only POSIX server-relative references, and resolve them against the orchestrator data directory before submission. Preserve basename sanitisation, storage-root symlink rejection, regular-file checks, and the local-handler allowlist.

**Verification.** With the orchestrator running and `inputs/missing.epub` absent, post a submission containing that server-relative path with `curl -sS -X POST "$ACHERON_URL/jobs" -H "Authorization: Bearer $ACHERON_REGISTRATION_TOKEN" -H "Content-Type: application/json" -d '{"source_type":"epub","source_path":"inputs/missing.epub","source_language":"en","target_language":"es","executor_strategy":"streaming","asr_model":null}'`; the response is HTTP 422 with `source_path not found`, and `acheron jobs` shows no newly created job, so the typo is surfaced before execution.

## OPS-027 — `resume --force-fresh` nukes the entire step cache

```yaml
---
id: OPS-027
title: "`resume --force-fresh` nukes the entire step cache; no per-step or per-chapter flag"
status: open
severity: medium
effort: M
discovered_via: [user-feedback, code-review]
user_facing_surface: cli
silent: true
journey_stage: t1
user_journey: "Operator runs a 100-chapter book, chapter 47 fails on TTS (model returned malformed audio), wants to re-run only chapter 47, runs `acheron job resume job-abc --force-fresh --step 47`, gets 'Error: no such option: --step'."
files:
  - path: src/acheron/cli.py
    lines: 219-227
  - path: src/acheron/shell/orchestrator.py
    lines: 718-722
related: [OPS-008, OPS-013]
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
feedback_ref: "TBD-pagerduty"
---
```

**Issue.** `src/acheron/cli.py:221` defines `--force-fresh` as a single boolean; no `--step` or `--chapter` filter exists. `orchestrator.py:718-722` deletes the entire `data_dir / job_id` directory.

**Why it matters.** For a 100-chapter book, the operator with one failed chapter must either re-run the whole book (waste 99 chapters of compute) or manually `rm` the per-step cache files.

**Recommendation.** Replace `--force-fresh` with `--invalidate-step <step_id>` (repeatable) and `--invalidate-chapter <n>` (repeatable). Accept `--force-fresh` as shorthand for "invalidate all."

**Verification.** `acheron job resume job-abc --invalidate-step step-47` re-runs only step 47.

## OPS-028 — TTS voice is worker-level config, not job-level

```yaml
---
id: OPS-028
title: "TTS voice is worker-level config (`WorkerCapabilities.metadata`), not job-level — user cannot pick per-chapter voices"
status: open
severity: medium
effort: L
discovered_via: [user-feedback, code-review]
user_facing_surface: cli
silent: true
journey_stage: t1
user_journey: "Operator submits a book, wants the first 3 chapters narrated by a female voice and chapters 4+ by a male voice, runs `acheron job submit book.epub --src en --dest es --voice 'vivian:1-3' --voice 'ryan:4-100'`, gets 'Error: no such option: --voice'."
files:
  - path: src/acheron/cli.py
    lines: 143-179
  - path: src/acheron/shell/api/schemas.py
    lines: 22-30
related: [OPS-015, OPS-010]
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
feedback_ref: "TBD-pagerduty"
---
```

**Issue.** TTS voice lives in `WorkerCapabilities.metadata` — it's a property of the worker, not the job. `SubmitJobRequest` has no `voice` field. `EpubRequest` / `AudioRequest` have no `voice` field.

**Why it matters.** This is a primary user feature for audiobooks: the operator wants different voices for different chapters.

**Recommendation.** Add `voice: str | None` to `EpubRequest` / `AudioRequest`. Add `--voice <name>` and `--voice-map '<chapter_range>: <voice>'` to `acheron job submit`.

**Verification.** `acheron job submit book.epub --src en --dest es --voice-map '1-3:vivian' --voice-map '4-100:ryan'` compiles a plan where steps 1-3 target TTS workers advertising `vivian` and steps 4-100 target workers advertising `ryan`.

## OPS-029 — `--asr` is optional for audio input

```yaml
---
id: OPS-029
title: "`--asr` is optional for audio input — operator can submit an `.mp3` with no ASR model and the plan compiles"
status: fixed
severity: medium
effort: S
discovered_via: [user-feedback, code-review]
user_facing_surface: cli
silent: true
journey_stage: t1
user_journey: "Operator runs `acheron job submit recording.mp3 --src en --dest es` (forgot `--asr`), sees 'Job submitted: job-abc12345'; the AudioRequest has `asr_model=None` (routes/jobs.py:42-48), the plan compiles anyway, and the first ASR step fails at runtime."
files:
  - path: src/acheron/cli.py
    lines: 247-280
  - path: src/acheron/shell/api/routes/jobs.py
    lines: 84-95
  - path: tests/shell/api/test_jobs.py
    lines: 557-583
  - path: tests/shell/test_cli.py
    lines: 72-87
related: [OPS-018, OPS-025, OPS-016]
fixed_in: [007a0427498ebb921f9273a4bdb9b3f0a66eee15, 8dce27f40763493b28bb6849d356db0b3e276fda]
verified_in: []
last_verified_at: {}
verified_by: ""
feedback_ref: "TBD-pagerduty"
---
```

**Issue.** Audio submissions now require a non-empty `asr_model` after trimming in the job route before source-path resolution or `orch.submit_job()`. Missing, blank, and whitespace-only models return HTTP 422 with `asr_model is required for source_type='audio'`; valid `--asr` values continue through the CLI upload and `/jobs` request.

**Why it matters.** Forgetting the ASR model fails immediately instead of creating a job that cannot execute its first ASR step.

**Recommendation.** Keep the audio guard at the submission boundary, normalize surrounding whitespace, and preserve the exact 422 detail. Ensure valid non-empty `--asr` values continue forwarding the selected ASR model without changing the upload or execution flow.

**Verification.** Starting with a valid `recording.mp3` and an audio input, run `acheron job submit recording.mp3 --src en --dest es` without `--asr`; the command exits 1 with `asr_model is required for source_type='audio'` and no job ID, prompting the operator to choose an ASR model before any job starts.

## OPS-031 — Dashboard `cost.html` has no time window, no "this week" aggregate

```yaml
---
id: OPS-031
title: "Dashboard's `partials/cost.html` renders every job ever; no time window, no \"this week\" aggregate row"
status: open
severity: medium
effort: S
discovered_via: [user-feedback, code-review]
user_facing_surface: dashboard
silent: false
journey_stage: t1
user_journey: "Operator opens the dashboard to check this week's cost, sees 200 rows (one per job ever), no 'this week' total, no filter."
files:
  - path: dashboard/templates/partials/cost.html
    lines: 1-53
  - path: dashboard/templates/index.html
    lines: 53-58
related: [OPS-005, OPS-012, OPS-001]
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
feedback_ref: "TBD-pagerduty"
---
```

**Issue.** `dashboard/templates/partials/cost.html:1-53` renders a per-job row for every job. There is no aggregation row, no "today / this week / this month" total, and no date column.

**Why it matters.** Cost is the operator's primary concern. A 200-row table with no aggregate is the wrong shape.

**Recommendation.** Add a `<tfoot>` row to `cost.html` showing `Total this week: $X.XX (N jobs)`. Add a small filter bar above the table: `Last 24h | 7d | 30d | All`.

**Verification.** With 200 jobs, the dashboard shows `Last 7d: $X.XX (12 jobs)` as the table footer.
