# Phase 4B Plan Preview

## Status

Approved design for the remaining Phase 4B Plan preview bundle. This document covers `OPS-011` and `OPS-016` only.

## Goals

- Let operators inspect a persisted plan by plan ID or job ID.
- Let operators validate a submission request before creating a job or starting execution.
- Keep preview validation identical to normal submission validation.
- Return a stable, typed API representation of plan structure.

## Non-goals

- Estimating cost.
- Assigning concrete worker IDs.
- Changing worker selection or scheduling.
- Adding Phase 4C job detail, cancellation, recovery, or live-monitoring behavior.

The current planner produces step types and dependencies, but does not calculate costs or persist concrete worker assignments. The preview therefore exposes the compile-time plan only.

## API design

### Plan response

Add public response schemas for a plan and its steps:

- `plan_id`
- `job_id`
- `source_type`
- `source_language`
- `target_language`
- `executor_strategy`
- `steps`

Each step exposes:

- `step_id`
- `worker_type`
- `depends_on`
- `status`

Internal step payloads remain private because they can contain resolved filesystem paths and are not needed by the operator-facing plan view.

### Plan lookup

Add `GET /plans/{plan_id}`. The orchestrator loads plans through `PlanCache`. A missing plan returns `404`; malformed or unreadable cached data is handled as a server-side cache failure. Plan identifiers are validated before filesystem access so a request cannot escape the configured data directory.

Only plans created by a real submission are persisted and available through this endpoint. Preview-only plans are returned in the response but are not saved.

### Submission preview

Add `POST /jobs:preview` using the same request body and validation rules as `POST /jobs`:

1. Validate executor strategy and source type.
2. Resolve the uploaded source path through the existing input boundary.
3. Validate the required ASR model for audio and reject invalid EPUB ASR options.
4. Compile the plan against current worker capabilities and chunking limits.
5. Return the typed plan without saving it, creating a tracked job, or scheduling execution.

Normal submission continues to save the plan, persist the tracked job, and schedule execution.

## Client and CLI design

Add client methods for plan lookup and preview. Both use the existing TLS and transport configuration; preview is a POST and therefore uses the configured registration token.

Add:

- `acheron job plan PLAN_ID`
- `acheron job plan --job JOB_ID`, which resolves the job first and then fetches its plan.
- `acheron job submit ... --dry-run`, which uploads the source and calls the preview endpoint instead of submitting it.

The plan command renders a concise table containing step ID, worker type, dependencies, and status. Dry-run renders the same plan summary and makes clear that no job was submitted.

## Errors

- Preserve existing domain validation and sanitized HTTP error messages.
- Return `404` for unknown jobs or plans.
- Return the existing validation status codes for malformed submission requests.
- Do not expose internal source paths or cache details in the plan response or CLI output.

## Testing

Add behavior-focused tests before implementation:

- Plan response schema round-trip and omission of internal payloads.
- `GET /plans/{plan_id}` success, missing plan, and safe plan-ID handling.
- `POST /jobs:preview` returns a plan and leaves both the job store and plan cache unchanged.
- Preview rejects the same invalid source, language, strategy, and ASR combinations as submission.
- Client request paths, payloads, response parsing, and bearer authentication.
- CLI plan lookup, job-ID lookup, dry-run output, and no-submission behavior.
- Existing submission behavior remains unchanged.

Required project gates remain `just lint-strict`, `just type-check`, `just test`, and `just validate`; the Phase 4 UX gate is `just ux-validate`.

## Metadata

After implementation and verification, update `docs/ux_review/ops.md` for `OPS-011` and `OPS-016`, recording implementation commits and journey verification according to the UX review specification.
