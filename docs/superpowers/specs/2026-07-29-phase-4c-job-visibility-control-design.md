# Phase 4C Job Visibility and Control

## Status

Approved design for the complete Phase 4C UX remediation program. This document consolidates the five Phase 4C bundles into one contract and one implementation target; the implementation plan stages the work so each stage remains independently testable.

## Goals

- Give operators one authoritative job response containing submission context, lifecycle timestamps, progress, outputs, and actionable errors.
- Make failed jobs diagnosable from the CLI and dashboard without reading orchestrator logs.
- Allow operators to cancel incorrect or stuck work while preserving partial results and metrics.
- Make retry and resume distinct operations: retry creates a new job, while resume continues a saved plan with targeted cache invalidation.
- Provide terminal-resident progress following and per-job event tailing.
- Keep every story independently verifiable while implementing the shared contracts in dependency order.

## Scope

Phase 4C includes these 13 stories:

| Stage | Bundle | Stories |
| --- | --- | --- |
| 1 | JobResponse envelope foundation | `OPS-004` |
| 2 | Job detail and failure attribution | `OPS-001`, `OPS-010`, `OPS-013`, `OPS-017`, `OPS-023` |
| 3 | Cancellation foundation | `OPS-008` |
| 4 | Control and recovery | `OPS-009`, `OPS-020`, `OPS-021`, `OPS-027` |
| 5 | Live monitoring | `OPS-002`, `OPS-014` |

Adjacent stories remain out of scope: `OPS-003`, `OPS-012`, `OPS-022`, and `OPS-028`. Phase 4C may consume their already-settled contracts, but does not expand their behavior beyond what the listed Phase 4C stories require. In particular, Phase 4C adds label filtering required by `OPS-017`, not the time-window, status, archive, or delete features of `OPS-012`.

## Constraints

- The project is greenfield. The Phase 4C wire and CLI contracts may change directly; no compatibility defaults, migration layer, or legacy command alias is required.
- The implementation follows TDD and the repository gates: `just lint-strict`, `just type-check`, `just test`, `just validate`, and `just ux-validate`.
- Story metadata is updated only after the implementation and story-scoped verification pass.
- The existing memory and Redis job stores remain supported and must persist the same logical job record.
- Operator-visible error messages remain sanitized; internal paths, credentials, URLs, and tracebacks do not enter public response fields.

## Architecture

The implementation is contract-first and staged:

1. Expand the shared public job contract and persist all required fields.
2. Consume that contract in CLI status and dashboard detail surfaces.
3. Add explicit orchestrator cancellation and partial-result persistence.
4. Add fresh-job retry, structured resume remediation, and selective cache invalidation.
5. Add a bounded progress event broker plus CLI follow/watch/tail surfaces.

The existing request flow remains the backbone:

```text
HTTP/CLI submission
  -> request normalization and source validation
  -> Orchestrator.submit_job
  -> TrackedJob persisted
  -> background execution
  -> progress/result persistence
  -> JobResponse conversion
```

All new surfaces use the same `TrackedJob` and `JobResponse` conversion path. No dashboard-specific job model is introduced.

## Public contracts

### Job response

`JobResponse` becomes the complete operator-facing representation:

```text
identity:     job_id, label, retries_from, plan_id
submission:   source_type, source_language, target_language,
              asr_model, executor_strategy
lifecycle:    status, created_at, last_persisted_at
progress:     completed_steps, total_steps, current_step, eta_seconds
result:       outputs
failures:     errors
advice:       warnings
```

Fields use the existing domain enums where applicable. Lifecycle timestamps are timezone-aware UTC datetimes and serialize as ISO 8601 values. `asr_model` is nullable for non-audio jobs. `label` and `retries_from` are nullable.

`OutputSummary` exposes only operator-relevant artifact data:

- `path`
- `filename`
- `size_bytes`
- `content_type`

The output checksum and internal metadata remain internal.

`StepError` contains:

- `step_id: str | None`
- `worker_type: WorkerType | None`
- `worker_id: str | None`
- `message: str`
- `timestamp: datetime`

Runtime step failures populate all available step and worker identity. Job-level failures such as operator cancellation use nullable identity fields.

`JobProgress` is the `JobResponse.progress` value. It contains the current step and worker identity, aggregate completed/total counts, and an optional ETA estimate. ETA is calculated from completed-step durations and remaining steps; it is absent until an estimate is meaningful.

### Internal persistence

`TrackedJob` gains:

- `label: str | None`
- `retries_from: str | None`
- `created_at: datetime`
- `last_persisted_at: datetime`
- current progress data needed to build `JobProgress`

`PlanResult.errors` changes from `tuple[str, ...]` to typed `StepError` values. The execution result path attaches the plan step and dispatch-selected worker identity before persisting an error. `JobResult` or its execution context is extended as needed to preserve the selected worker ID.

The memory and Redis stores serialize and restore the complete record, including timestamps, labels, retry linkage, typed errors, outputs, and current progress. A store write updates `last_persisted_at`; `created_at` is assigned once when the job is created.

### Requests

`SubmitJobRequest` gains `label: str | None`.

Retry uses a separate strict request with optional overrides for the original submission fields:

- `source_path`
- `source_language`
- `target_language`
- `executor_strategy`
- `asr_model`
- `label`

The server starts from the original stored request, applies supplied overrides, validates the resulting request through the normal submission path, compiles a fresh plan, and creates a new job with `retries_from` set to the original ID.

Resume replaces the boolean whole-job invalidation request with repeated explicit selections:

- `invalidate_steps: list[str]`
- `invalidate_chapters: list[int]`

There is no `force_fresh` compatibility path. If neither selection is supplied, resume retains the existing cached outputs. If a selection is supplied, the invalidation closure includes the selected cache entries and all dependent downstream steps required for a correct plan result.

Chapter invalidation is truthful only for readable EPUB sources. Planning discovers the same non-empty EPUB spine documents that extraction names `chapter_001`, `chapter_002`, and so on; audio jobs have no chapter identities. If planning cannot read the source, the plan carries no chapter metadata and resume returns an explicit source-metadata limitation instead of guessing. Because the current cache is stage-level rather than chapter-level, a chapter selection invalidates the stage steps associated with that chapter and their downstream dependents.

### Structured errors

Domain failures use a structured response body:

```json
{
  "type": "JobAlreadyRunningError",
  "message": "Job job-abc is already running",
  "remediation": "acheron job cancel job-abc"
}
```

`remediation` is nullable. The CLI renders it as a copy-pasteable `Try:` line. `JobAlreadyRunningError` recommends cancellation; the missing-plan resume error recommends re-submission. Cancellation of a terminal job and invalid retry/resume requests use the same structured error mechanism.

## Lifecycle and API behavior

### Routes

- `POST /jobs` submits a job and accepts `label`.
- `GET /jobs` lists jobs and accepts the Phase 4C `label` glob filter.
- `GET /jobs/{job_id}` returns the complete `JobResponse`.
- `POST /jobs/{job_id}/cancel` cancels pending or running work and returns the terminal `JobResponse`.
- `POST /jobs/{job_id}/retry` applies optional request overrides and returns the new job's `JobResponse`.
- `POST /jobs/{job_id}/resume` resumes an incomplete saved plan with explicit step/chapter invalidation lists.
- `GET /jobs/{job_id}/logs?follow=true` streams `JobLogEvent` values as newline-delimited JSON.
- `GET /jobs/{job_id}/outputs/{filename}` serves only an artifact listed for the requested job and only from the configured data directory. Filename/path validation prevents traversal and cross-job access.

### State transitions

- Submission persists the job before background execution starts.
- A pending or running job may be cancelled. Cancellation is serialized with execution using the existing per-job lock.
- Operator cancellation produces `FAILED`, preserves completed outputs, costs, duration, and progress, and adds a typed error whose message is `cancelled by operator`.
- Cancellation waits for shielded persistence before returning. Shutdown cancellation remains a separate internal reason.
- A completed job is not mutated by retry. Retry creates a new job and leaves the source job unchanged.
- Resume is allowed for incomplete terminal jobs with a saved plan. A running job returns `JobAlreadyRunningError`; a missing plan returns `NoPlanToResumeError` with a re-submit remediation.
- Resume invalidates only the selected step/chapter cache closure, then reuses all remaining valid outputs.
- Terminal jobs remain queryable through `GET /jobs/{job_id}` and retain their final `last_persisted_at`.

## CLI behavior

The CLI mirrors the HTTP operations:

- `acheron jobs [--label LABEL_GLOB]`
- `acheron job submit … [--label LABEL] [--follow]`
- `acheron job status ID [--verbose]`
- `acheron job watch ID`
- `acheron job cancel ID`
- `acheron job retry ID [--src …] [--dest …] [--asr …] [--label …]`
- `acheron job resume ID [--invalidate-step STEP] [--invalidate-chapter N]`
- `acheron job tail ID`

`job status` renders submission metadata, lifecycle timestamps, progress, output summaries, labels/retry linkage, and structured errors. Verbose output includes step and worker attribution.

`job watch` and `job submit --follow` share a Rich live renderer. They poll `get_job()` every two seconds, display a progress bar, current step, estimated remaining time when available, and the most recent error. They exit with status 0 for `COMPLETED` and status 1 for `FAILED` or `PARTIAL`. They do not cancel the remote job when the local observer exits.

`job tail` consumes the NDJSON endpoint and renders one progress line per event. It closes after the terminal event. Ctrl-C stops observation only.

## Dashboard behavior

The dashboard keeps the current HTMX polling model and adds:

- `label` and last-error columns to the jobs table.
- A clickable row path to a per-job detail fragment.
- Detail rendering for submission metadata, lifecycle timestamps, progress, outputs, and errors.
- An error table containing `step_id`, `worker_type`, `worker_id`, `message`, and `timestamp`.
- Safe output links through the allowlisted output route.

The dashboard distinguishes an orchestrator fetch failure from a failed job. Existing status-fragment behavior and polling cadence remain unchanged unless a focused test requires a route integration change.

## Progress event stream

The orchestrator owns one bounded per-job event broker. Execution emits events for:

- step start
- step completion
- step failure
- operator cancellation
- terminal reconciliation

`JobLogEvent` contains the job ID, UTC timestamp, step/worker identity where available, progress counters, status, and a sanitized message. The logs route sends a current snapshot first, then live events, and closes after the terminal event. The broker is an observation surface and is not the durable job record; after restart, `JobResponse` remains authoritative.

## Stage completion criteria

### Stage 1 — JobResponse envelope foundation

`OPS-004` is complete when submission parameters, executor strategy, labels/retry linkage, UTC lifecycle timestamps, outputs, typed errors, and progress data round-trip through both job stores and appear in API/client responses.

### Stage 2 — Job detail and failure attribution

`OPS-001`, `OPS-010`, `OPS-013`, `OPS-017`, and `OPS-023` are complete when CLI status exposes outputs and metadata, labels can be submitted and filtered, dashboard rows open detail, and failed steps display worker attribution and timestamps.

### Stage 3 — Cancellation foundation

`OPS-008` is complete when canceling an active job exits successfully, produces `FAILED` with `cancelled by operator`, preserves partial state, and leaves no execution task able to overwrite the cancellation result.

### Stage 4 — Control and recovery

`OPS-009`, `OPS-020`, `OPS-021`, and `OPS-027` are complete when retry creates a linked new job, resume failures provide actionable commands, and targeted invalidation reruns only the selected cache closure.

### Stage 5 — Live monitoring

`OPS-002` and `OPS-014` are complete when submit-follow/watch render live progress with correct terminal exit codes and job tail streams sanitized progress events until terminal completion.

## Testing and verification

Every stage starts with failing tests and uses focused tests before the full gates.

Coverage includes:

- Core schema/domain tests for all new response models, typed errors, timestamps, outputs, labels, retry linkage, progress, and NDJSON events.
- Memory and Redis store round-trip tests for the expanded `TrackedJob` record.
- Orchestrator tests for cancellation races, partial persistence, retry isolation, remediation errors, event emission, and dependent cache invalidation.
- API and client tests for every new route, strict request shape, authentication, structured errors, output serving, and streaming behavior.
- CLI tests for status rendering, labels, retry overrides, invalidation options, follow/watch polling, tail consumption, and exit codes.
- Dashboard tests for row navigation, detail rendering, output links, fetch failures, and per-step attribution.
- Integration journeys for completed, failed, cancelled, retried, selectively resumed, followed, and tailed jobs.

The final verification sequence is:

```bash
just lint-strict
just type-check
just test
just validate
just ux-validate
just first-run
just ux-verify OPS-004
just ux-verify OPS-001
just ux-verify OPS-010
just ux-verify OPS-013
just ux-verify OPS-017
just ux-verify OPS-023
just ux-verify OPS-008
just ux-verify OPS-009
just ux-verify OPS-020
just ux-verify OPS-021
just ux-verify OPS-027
just ux-verify OPS-002
just ux-verify OPS-014
```

`just first-run` is extended only where the Compose-backed dashboard path needs coverage. The Phase 3a simulator and `runpod-bootstrap` are not required unless an implementation stage changes their exercised surfaces.

After implementation verification, update each story's `fixed_in`, `verified_in`, `last_verified_at`, and `verified_by` fields and refresh aggregate UX summary counts. Finish with an independent correctness and documentation-staleness review.

## Non-goals

- Backward-compatible response parsing or command aliases.
- Time-window/status/archive/delete job management from `OPS-012`.
- A general log aggregation system or durable worker stdout archive.
- Changing worker scheduling, capability discovery, cost calculation, or voice selection.
- Cancelling a job as a side effect of local follow/watch/tail interruption.
