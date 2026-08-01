---
theme: OPS
last_updated_date: 2026-08-01
version: 3
---

# OPS

**Grade**: C (1 high + 1 medium + 1 low unresolved story)
**Calibration target**: an operator should be able to submit, monitor, debug, and recover a job without `docker logs`.

## OPS-001 — Dashboard renders only three read-only tables

```yaml
---
id: OPS-001
title: Dashboard renders only three read-only tables; clicking a job row does nothing; no Last-error column
status: fixed
severity: high
effort: M
discovered_via: [code-review, on-call]
user_facing_surface: dashboard
silent: false
journey_stage: t1
user_journey: "Operator opens the dashboard during a 2-hour translation run, sees a row with a FAILED job, clicks the row, expects a detail page with start time, end time, error string, and at least one output link; gets nothing — the click does not navigate."
files:
  - path: dashboard/app.py
    lines: 81-88
  - path: dashboard/templates/partials/jobs.html
    lines: 1-40
related: [OBS-002]
fixed_in: [7e62b0c, ff0b4de, 9ac320c]
verified_in: []
last_verified_at: {}
verified_by: ""
incident_ref: "TBD-pagerduty"
---
```

**Current state.** The dashboard now renders clickable job rows and a detail partial with job metadata, errors, and output links.

**Verification.** Open the dashboard, click a job row, and confirm the detail view renders instead of leaving the operator on a flat table.

## OPS-002 — CLI has no `watch` / `follow` mode

```yaml
---
id: OPS-002
title: "`acheron job submit` returns immediately; operator must wrap `watch -n 2 acheron job status` manually"
status: fixed
severity: high
effort: S
discovered_via: [user-feedback, code-review]
user_facing_surface: cli
silent: true
journey_stage: t1
user_journey: "Operator runs `acheron job submit book.epub --src en --dest es --follow`, expects a live progress bar that updates every 2s; gets `Job submitted: job-abc12345` and the prompt returns immediately."
files:
  - path: src/acheron/cli.py
    lines: 362-439
  - path: src/acheron/api_client.py
    lines: 91-98
related: [OPS-014]
fixed_in: [97a4cea]
verified_in: []
last_verified_at: {}
verified_by: ""
feedback_ref: "TBD-pagerduty"
---
```

**Current state.** Submission supports follow-up progress rendering and the API client exposes the required polling/event surface.

**Verification.** Submit with `--follow` and confirm the command remains attached while progress updates arrive.

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
    lines: 113-133
  - path: src/acheron/shell/api/routes/jobs.py
    lines: 57-64
related: [SEC-006, SEC-012, SEC-019]
fixed_in: ["6992588"]
verified_in: []
last_verified_at: {}
verified_by: ""
feedback_ref: "TBD-pagerduty"
---
```

**Current state.** Unsupported-language failures are rendered as actionable CLI errors with supported-language guidance.

**Verification.** Submit with an unsupported destination language and confirm the command exits non-zero with remediation rather than an opaque traceback.

## OPS-004 — `JobResponse` carries no submission params or timestamps

```yaml
---
id: OPS-004
title: "`JobResponse` schema has no `source_type`, `source_language`, `target_language`, `asr_model`, `created_at`, `last_persisted_at`"
status: fixed
severity: high
effort: S
discovered_via: [code-review, on-call]
user_facing_surface: cli
silent: true
journey_stage: t1
user_journey: "Operator submits a job with `--src en --dest es --asr whisper-v3`, later runs `acheron job status job-xyz`, sees the response has no `source_language` / `target_language` / `asr_model` / `created_at` fields; cannot tell which ASR model was used or when the job started."
files:
  - path: src/acheron/core/schemas.py
    lines: 70-99
  - path: src/acheron/shell/api/routes/jobs.py
    lines: 433-489
related: [TYPE-005]
fixed_in: [7201c4c, 09ab91a, e08bb25, 1cefba5]
verified_in: []
last_verified_at: {}
verified_by: ""
incident_ref: "TBD-pagerduty"
---
```

**Current state.** Job responses include the submission metadata needed to explain how a job was created.

**Verification.** Inspect job status and confirm the response includes the persisted request fields and timestamps.

## OPS-005 — Cost basis labels rendered without explanation

```yaml
---
id: OPS-005
title: "Dashboard's `MEASURED` / `CACHED` / `UNKNOWN` / `STATIC` cost-basis badges are rendered with no tooltip or legend"
status: verified
severity: high
effort: S
discovered_via: [code-review, user-feedback]
user_facing_surface: dashboard
silent: true
journey_stage: t1
user_journey: "Operator hovers the `MEASURED` badge on a cost row, sees a tooltip: 'MEASURED: just asked RunPod for the rate. CACHED: last-known rate; GraphQL unavailable. UNKNOWN: no rate available. STATIC: fixed $/hr or zero (stub/local).'"
files:
  - path: dashboard/templates/partials/cost.html
    lines: 1-53
  - path: src/acheron/core/models.py
    lines: 69-75
related: [CORR-008, CORR-040, TYPE-005]
fixed_in: [0d6bd414ce745f2483fbd1af76beb010f5178aba, CURRENT_HEAD]
verified_in: [0d6bd414ce745f2483fbd1af76beb010f5178aba, CURRENT_HEAD]
last_verified_at:
  commit: CURRENT_HEAD
  tree: cec614c3d0c704df6253eafeede9d8097288d754b8001426311af6af120361cd
  date: "2026-07-31"
verified_by: "harness:pricing-outage+gpu-switch+focused-tests"
feedback_ref: "TBD-pagerduty"
---
```

**Issue.** `dashboard/templates/partials/cost.html:17-30` renders four basis labels with no tooltip. The `STATIC` basis covers two very different cases: a fixed `$/hr` AND `$0.00` (stub). An operator who sees `$0.00` and a `STATIC` badge reads it as "free."

**Why it matters.** Cost is a primary operator concern. The badge is a signal that something might be off, but a first-time operator has no way to learn the signal.

**Recommendation.** (a) Add a `?` icon next to each badge that opens a tooltip with the four-state legend. (b) Split `STATIC` into `STATIC` (fixed $/hr) and `ZERO` (stub/local). (c) Add a CLI: `acheron job cost <id> --explain`.

**Verification.** Focused CLI/dashboard tests cover the estimated-cost label, basis explanation, and STUB distinction; the pricing-outage simulation proves only explicit `price_source=zero` emits STUB.

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
    lines: 74-95
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
status: fixed
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
fixed_in: [002b810, 46c13bd, 93c3858, af92084, 89f3ade]
verified_in: []
last_verified_at: {}
verified_by: ""
feedback_ref: "TBD-pagerduty"
---
```

**Current state.** Operators can cancel jobs through the CLI and API, with structured errors for jobs that cannot be cancelled.

**Verification.** Cancel a running job and confirm the command reports the resulting terminal state and remediation for invalid requests.

## OPS-009 — No `acheron job retry` with edited parameters

```yaml
---
id: OPS-009
title: "No `acheron job retry` with edited parameters — `resume` re-runs the same plan"
status: fixed
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
fixed_in: ["2647367", "2610032", "065daa0"]
verified_in: []
last_verified_at: {}
verified_by: ""
feedback_ref: "TBD-pagerduty"
---
```

**Current state.** Resume requests expose structured remediation and preserve the retry contract.

**Verification.** Resume a job and confirm the command either starts the retry or reports a typed, actionable failure.

## OPS-010 — `job status` shows `completed` but no output download URL

```yaml
---
id: OPS-010
title: "`acheron job status` shows `completed` but no output download URL — operator cannot find the audiobook"
status: verified
severity: high
effort: S
discovered_via: [user-feedback, code-review]
user_facing_surface: cli
silent: true
journey_stage: t1
user_journey: "Operator runs `acheron job status job-abc12345`, sees `Status: completed`, expects a `Download URL` for the produced artifact; gets only the status badge and counters."
files:
  - path: src/acheron/cli.py
    lines: 493-494
  - path: src/acheron/core/schemas.py
    lines: 19-25
related: [OPS-028, OPS-001]
fixed_in: ["4750302", "1f5514c", "CURRENT_HEAD"]
verified_in: ["1f5514c", "CURRENT_HEAD"]
last_verified_at:
  commit: CURRENT_HEAD
  tree: cec614c3d0c704df6253eafeede9d8097288d754b8001426311af6af120361cd
  date: "2026-07-30"
verified_by: "harness:pytest+just-validate"
feedback_ref: "TBD-pagerduty"
---
```

**Issue.** `src/acheron/cli.py:493-494` renders each persisted output's public `download_url`, while `JobResponse` exposes output metadata without leaking the server filesystem path.

**Why it matters.** The deliverable artifact is the entire point of the pipeline, and remote operators need an HTTP resource rather than a path on the orchestrator host.

**Recommendation.** Keep `outputs: list[OutputSummary]` on `JobResponse` with a server-relative `download_url`, display filename, size, and content type.

**Verification.** Submit and complete a job; `acheron job status <id> --verbose` shows `Download URL: /jobs/<id>/outputs/<index> (size, content-type)` and the URL serves the selected artifact.

## OPS-011 — `plan_id` is exposed but no `acheron job plan` command

```yaml
---
id: OPS-011
title: "`JobResponse.plan_id` is exposed but there is no `acheron job plan` command to inspect it"
status: fixed
severity: medium
effort: S
discovered_via: [code-review, user-feedback]
user_facing_surface: cli
silent: true
journey_stage: t1
user_journey: "Operator runs `acheron job status job-abc`, sees `Plan: plan-9c0a1b`, wants to know what step structure the plan requires, runs `acheron job plan plan-9c0a1b`, expects a table of step_id / worker_type / depends_on / status showing the required worker types per step and their dependencies (no concrete worker IDs — assigning concrete workers is explicitly a non-goal for this plan preview surface; the user approved the reduced structure-only contract)."
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

**Refresh note.** The plan command exists, but its original verification is invalidated by later changes to cited files. The story remains fixed pending a fresh journey verification.

**Verification.** Run `acheron job plan <plan-id>` from a completed job and confirm the plan structure is rendered.

## OPS-012 — `acheron jobs` has no time-window filter, no status filter, no archive/delete

```yaml
---
id: OPS-012
title: "`acheron jobs` has no time-window filter, no status filter beyond binary --active/--completed, no archive/delete"
status: verified
severity: medium
effort: S
discovered_via: [user-feedback, code-review]
user_facing_surface: cli
silent: false
journey_stage: t1
user_journey: "Operator runs `acheron jobs` after a month of use, sees 200 rows, wants `acheron jobs --since 24h` to see only today's jobs, then `acheron jobs --archive job-old1 job-old2` to prune the table."
files:
  - path: src/acheron/cli.py
    lines: 633-657
related: [OPS-031, OPS-004]
fixed_in: [32335ce, CURRENT_HEAD]
verified_in: [32335ce, CURRENT_HEAD]
last_verified_at:
  commit: CURRENT_HEAD
  tree: cec614c3d0c704df6253eafeede9d8097288d754b8001426311af6af120361cd
  date: "2026-07-30"
verified_by: "harness:phase-4d-task-10-recovery"
drift_note: "Time-window/status/archive controls and archive metadata are covered by the Task 10 recovery journey."
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
status: fixed
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
fixed_in: [ea768e6, 5f10f6a, d32c494]
verified_in: []
last_verified_at: {}
verified_by: ""
feedback_ref: "TBD-pagerduty"
---
```

**Current state.** Step errors carry worker attribution in the public response.

**Verification.** Inspect a failed job and confirm each step error includes its worker ID when available.

## OPS-014 — No `acheron job tail` / `acheron job log` — operator cannot see what the worker is doing

```yaml
---
id: OPS-014
title: "No `acheron job tail` / `acheron job log` — operator cannot see what the worker is doing right now"
status: fixed
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
fixed_in: [6376a4b, 4e82c66, a24aeda]
verified_in: []
last_verified_at: {}
verified_by: ""
feedback_ref: "TBD-pagerduty"
---
```

**Current state.** CLI progress and event handling expose the job lifecycle without requiring direct orchestrator logs.

**Verification.** Follow a running job and confirm progress events render as they arrive.

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
status: fixed
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

**Refresh note.** The preview command exists, but its original verification is invalidated by later changes to cited files. The story remains fixed pending a fresh journey verification.

**Verification.** Run `acheron job submit ... --dry-run` and confirm a plan is printed without persistence.

## OPS-017 — Jobs have no human-readable name

```yaml
---
id: OPS-017
title: "Jobs have no human-readable name; `job-abc12345` is the only identity"
status: obsolete
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

**Resolution.** Labels are now exposed in the job response and status surfaces; the former no-name complaint is superseded by the label contract.

**Verification.** Submit a labeled job and confirm the label is visible in job listings and detail output.

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
status: obsolete
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

**Resolution.** Generic structured error remediation now points operators toward cancelling a running job when resume is invalid.

**Verification.** Attempt to resume a running job and confirm the response provides the cancellation remediation.

## OPS-021 — `resume` on a no-plan job — error has no "re-submit" hint

```yaml
---
id: OPS-021
title: "`resume` on a job with no saved plan — error string doesn't tell the operator what to do"
status: obsolete
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

**Resolution.** No-plan resume failures now include the re-submit remediation, superseding this narrowly scoped complaint.

**Verification.** Attempt to resume a job without a saved plan and confirm the response explains how to re-submit it.

## OPS-022 — 4xx/5xx from wrong base URL doesn't echo the attempted URL

```yaml
---
id: OPS-022
title: 4xx/5xx from the wrong base URL doesn't echo the attempted URL — operator can't tell they hit a different host
status: verified
severity: medium
effort: S
discovered_via: [user-feedback, on-call]
user_facing_surface: cli
silent: true
journey_stage: t1
user_journey: "Operator's `ACHERON_URL` env var points to a stale deploy, `acheron job status job-abc` returns `Error 404: Not Found` (from the wrong host), has no idea they hit the wrong orchestrator."
files:
  - path: src/acheron/cli.py
    lines: 213-215
  - path: src/acheron/api_client.py
    lines: 91-98
related: [OPS-003]
fixed_in: [185efcb, de85647, 215109c, 8f04f5c, c9b8710, 9e86939, CURRENT_HEAD]
verified_in: [9e86939, CURRENT_HEAD]
last_verified_at:
  commit: CURRENT_HEAD
  tree: cec614c3d0c704df6253eafeede9d8097288d754b8001426311af6af120361cd
  date: "2026-07-30"
verified_by: "harness:phase-4d-task-12-correlation"
feedback_ref: "TBD-pagerduty"
incident_ref: "TBD-pagerduty"
---
```

**Current state.** HTTP status failures include the attempted URL and an `ACHERON_URL` remediation hint; streamed and regular requests also surface the response correlation ID without exposing credentials.

**Why it matters.** "Is the job gone?" vs "did I hit the wrong host?" is the operator's first triage question.

**Verification.** Task 12 focused CLI/API tests cover status-error URL diagnostics, URL sanitization, bounded error content, and request-ID output. The dashboard version journey also confirms orchestrator URLs and configuration fields are not rendered.

## OPS-023 — Dashboard per-job detail lacks per-step worker attribution

```yaml
---
id: OPS-023
title: "Failed step's `worker_id` is invisible in dashboard's `partials/jobs.html` — only a flat error column will surface it"
status: obsolete
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

**Resolution.** Dashboard detail and structured step errors now expose per-job and per-step diagnostics; this complaint is superseded by OPS-001 and OPS-013.

**Verification.** Open job detail and confirm step errors include worker attribution when available.

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
status: fixed
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
fixed_in: [723b743, 15814c6]
verified_in: []
last_verified_at: {}
verified_by: ""
feedback_ref: "TBD-pagerduty"
---
```

**Current state.** Resume supports targeted invalidation with `--invalidate-step`, avoiding the previous whole-cache reset behavior.

**Verification.** Resume with `--invalidate-step step-47` and confirm only the selected step is invalidated.

## OPS-028 — TTS voice is worker-level config, not job-level

```yaml
---
id: OPS-028
title: "TTS voice is worker-level config (`WorkerCapabilities.metadata`), not job-level — user cannot pick per-chapter voices"
status: verified
severity: medium
effort: L
discovered_via: [user-feedback, code-review]
user_facing_surface: cli
silent: true
journey_stage: t1
user_journey: "Operator submits a four-chapter book with `acheron job submit book.epub --src en --dest es --voice-map 1-3:Vivian --voice-map 4-4:Ryan`, sees a successful job submission, and the persisted plan uses one jointly capable TTS worker with the requested chapter voices."
files:
  - path: src/acheron/cli.py
    lines: 674-820
  - path: src/acheron/api_client.py
    lines: 316-343
  - path: src/acheron/shell/api/routes/jobs.py
    lines: 185-265
  - path: src/acheron/shell/api/routes/jobs.py
    lines: 465-490
  - path: src/acheron/core/planner.py
    lines: 69-82
  - path: src/acheron/core/planner.py
    lines: 195-215
  - path: workers/qwen3tts/handler.py
    lines: 139-171
  - path: tests/integration/test_job_lifecycle.py
    lines: 520-698
  - path: tests/shell/api/test_jobs.py
    lines: 1769-1830
  - path: tests/shell/test_cli.py
    lines: 583-720
related: [OPS-015, OPS-010]
fixed_in: [8d3229a, CURRENT_HEAD]
verified_in: [8d3229a, CURRENT_HEAD]
last_verified_at:
  commit: CURRENT_HEAD
  tree: cec614c3d0c704df6253eafeede9d8097288d754b8001426311af6af120361cd
  date: "2026-07-31"
verified_by: "harness:task-17-voice-journey"
feedback_ref: "TBD-pagerduty"
---
```

**Current state.** Job-level voice selection accepts a canonical default voice or an inclusive EPUB chapter voice map. A successful preview retains the temporary upload for submission; rejected or failed preflight paths delete it before any job or plan persists.

**Why it matters.** Operators can assign voices per chapter while retaining deterministic worker selection and actionable preflight failures.

**Verification.** The Task 17 journey creates a four-chapter numbered EPUB, uploads it as a temporary input, runs `POST /jobs:preview`, and submits the same input only after preview succeeds. The persisted plan carries canonical `Vivian`/`Ryan` voice-map data and selects one TTS worker advertising both voices. The Qwen boundary receives the expected speaker sequence `Vivian, Vivian, Vivian, Ryan`; a fleet with separate Vivian-only and Ryan-only workers fails during preview with no input, job, or plan leak.

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
status: verified
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
    lines: 58-63
related: [OPS-005, OPS-012, OPS-001]
fixed_in: [0d6bd414ce745f2483fbd1af76beb010f5178aba, CURRENT_HEAD]
verified_in: [0d6bd414ce745f2483fbd1af76beb010f5178aba, CURRENT_HEAD]
last_verified_at:
  commit: CURRENT_HEAD
  tree: cec614c3d0c704df6253eafeede9d8097288d754b8001426311af6af120361cd
  date: "2026-07-31"
verified_by: "harness:pricing-outage+gpu-switch+focused-tests"
feedback_ref: "TBD-pagerduty"
---
```

**Issue.** `dashboard/templates/partials/cost.html:1-53` renders a per-job row for every job. There is no aggregation row, no "today / this week / this month" total, and no date column.

**Why it matters.** Cost is the operator's primary concern. A 200-row table with no aggregate is the wrong shape.

**Recommendation.** Add a `<tfoot>` row to `cost.html` showing `Total this week: $X.XX (N jobs)`. Add a small filter bar above the table: `Last 24h | 7d | 30d | All`.

**Verification.** Focused dashboard and API tests cover the 24h/7d/30d/all windows, aggregate total, and unknown-job count; all five Stage 1 UX verification commands pass after metadata update.

## OPS-032 — `acheron job tail` leaks a traceback for HTTP errors

```yaml
---
id: OPS-032
title: "`acheron job tail` leaks a traceback for HTTP errors instead of a structured CLI failure"
status: open
severity: medium
effort: S
discovered_via: [code-review]
user_facing_surface: cli
silent: true
journey_stage: t1
user_journey: "Operator runs `acheron job tail missing-job`, expects a concise non-zero error with remediation to inspect `acheron jobs`, but receives a raw HTTP traceback."
files:
  - path: src/acheron/cli.py
    lines: 136-163
  - path: src/acheron/api_client.py
    lines: 109-119
related: []
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
---
```

**Issue.** `_run_sync_generator()` does not route `HTTPStatusError` through the CLI error renderer, so `acheron job tail` exposes a traceback for missing jobs and other API failures.

**Why it matters.** Operators receive implementation details instead of a recoverable command-level error.

**Recommendation.** Render tail API failures through the same structured CLI error path and include remediation such as `acheron jobs`.

**Verification.** `acheron job tail missing-job` exits non-zero without a traceback and prints actionable remediation.

## OPS-033 — Dashboard job detail URL is a partial, not a durable dashboard page

```yaml
---
id: OPS-033
title: "Dashboard job detail URL is a partial, not a durable dashboard page"
status: open
severity: low
effort: S
discovered_via: [code-review]
user_facing_surface: dashboard
silent: false
journey_stage: t1
user_journey: "Operator clicks a job row, reloads or shares the resulting URL, and expects the full dashboard shell with the selected detail view; the URL returns only the partial fragment."
files:
  - path: dashboard/templates/partials/jobs.html
    lines: 11-17
  - path: dashboard/app.py
    lines: 86-88
related: [OPS-001]
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
---
```

**Issue.** HTMX pushes `/partials/jobs/<id>` into browser history, so reloads and shared links return a fragment without the dashboard shell.

**Why it matters.** Operators cannot reliably bookmark or share the job detail view.

**Recommendation.** Make the detail URL resolve to a durable dashboard page while retaining partial navigation for in-page updates.

**Verification.** Click a job, reload the resulting URL, and open it in a new session; the dashboard shell and selected detail remain visible.
