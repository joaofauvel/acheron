# Phase 4D Cost and Long-Term Operations

## Status

Approved design for the complete Phase 4D UX remediation program. This document consolidates all four Phase 4D bundles into one contract and implementation target; the implementation plan will stage the work so each bundle remains independently testable.

## Goals

- Make job cost explanations truthful, explicit, and useful for operational forensics.
- Give operators safe administrative recovery tools for stale jobs, retention, and worker health history.
- Make deployed-version identity and request correlation visible across API, CLI, and dashboard surfaces.
- Allow job-level voice selection for EPUB and audio jobs without expanding the executor into a branch-aware DAG.
- Preserve one authoritative contract across the orchestrator, API client, CLI, dashboard, memory store, and Redis store.
- Keep every story independently verifiable while sharing only the contracts required by the phase.

## Scope

Phase 4D includes these 15 stories:

| Stage | Bundle | Stories |
| --- | --- | --- |
| 1 | Cost truth | `MAINT-014`, `MAINT-015`, `OPS-005`, `OPS-031`, `MAINT-002` |
| 2 | Recovery administration | `MAINT-001`, `MAINT-008`, `MAINT-010`, `MAINT-011`, `MAINT-012`, `OPS-012` |
| 3 | Traceability and deployed-version visibility | `OPS-022`, `MAINT-013`, `MAINT-016` |
| 4 | Job-level voice selection | `OPS-028` |

Adjacent stories remain out of scope. In particular, Phase 4D does not implement registration-token rotation from `MAINT-007`, certificate lifecycle work from `MAINT-003` through `MAINT-005`, registration lifecycle work from `MAINT-006` and `MAINT-007`, worker-image cache validation from `MAINT-017`, or a generalized role/JWT authorization system.

## Design decisions

- Use a small shared-contract approach with isolated delivery stages; do not introduce a generic operations framework.
- Add a separate `ACHERON_ADMIN_TOKEN` for destructive `/admin/*` mutations. The registration token is distributed to workers and must not authorize administrative actions. Registration and admin tokens share an explicit validation policy: minimum 32 characters, reject known public/example values, and allow absence only where the corresponding feature is optional; read-only startup never requires the admin token.
- `ACHERON_OPEN_REGISTRATION=1` never bypasses admin authorization. A missing admin token produces an unavailable-admin response rather than an open administrative mode.
- Archive and cleanup are distinct. Archive hides a job from the default list while preserving its record and artifacts. Cleanup is irreversible deletion and requires an explicit apply operation.
- Cost is a rate-at-execution estimate, not invoice truth. The system must not call RunPod's lowest-price query an actual billed rate.
- Voice maps use one selected TTS worker that jointly advertises every requested voice. The phase does not add branch-aware execution or multi-worker TTS fan-out.
- The project is greenfield. Public contracts and commands may change directly; no compatibility aliases, fallback authorization paths, or legacy command modes are required.

## Architecture

The existing orchestrator remains the source of truth:

```text
CLI / dashboard / API
        |
        +-- read-only diagnostics
        |     +-- cost explanations and aggregates
        |     +-- job filters
        |     +-- deployed version and request correlation
        |
        +-- registration-token mutations
        |     +-- normal job and worker operations
        |
        +-- admin-token mutations
              +-- stale-job recovery
              +-- archive and cleanup
              +-- operational disk and worker-history administration
```

The data flow remains contract-first:

```text
worker execution
  -> enriched JobMetrics
  -> executor PlanResult
  -> persisted TrackedJob
  -> JobResponse / cost response
  -> CLI and dashboard
```

Administrative operations use the existing job and worker stores. Memory and Redis stores persist equivalent logical records and expose equivalent filtering, archival, error-history, and deletion behavior. No dashboard-specific job model is introduced.

Delivery order is:

1. Cost truth and cost reporting.
2. Recovery administration and retention.
3. Request traceability and deployed-version identity.
4. Job-level voice selection.

The admin authorization primitive is introduced before the first administrative route. All admin actions include the request ID, action name, operator-supplied reason where applicable, affected IDs or count, and final result in structured logs.

## Public and internal contracts

### Cost estimate

The worker-side `PriceEstimate` and the persisted cost representation carry:

```text
cost: float | None
basis: measured | cached | static | stub | unknown
rate_per_hour: float | None
gpu_type: str | None
secure_cloud: bool | None
queried_at: UTC datetime | None
cache_age_seconds: float | None
```

`STUB` is a distinct cost basis for local or zero-price workers. `STATIC` means an operator-configured fixed rate. `UNKNOWN` means no usable rate was available. `MEASURED` means a fresh provider query supplied the rate, and `CACHED` means the last-known rate was used after refresh failure.

A per-step `CostBreakdown` item adds:

- `step_id`
- `worker_type`
- `worker_id`
- `gpu_seconds`
- the structured estimate above

`JobMetrics` carries the structured estimate for its single worker step. `PlanResult` owns the accumulated step-identified `CostBreakdown` tuple, and `TrackedJob` retains that tuple for restart reconstruction. A cache hit with no pricing attempt produces no breakdown item; an attempted execution with no usable rate produces an `UNKNOWN` `CostEstimate` item so missing pricing is visible. Aggregate basis uses the least-confidence order `MEASURED` > `CACHED` > `STATIC` > `STUB` > `UNKNOWN`; unknown items count toward unknown reporting but never contribute zero dollars. `JobResponse` retains `total_cost` and `total_cost_basis` and adds `cost_breakdown`. Cost values are execution-time estimates based on the configured provider rate and measured handler duration; no response field claims to be an invoice amount.

The RunPod implementation records the GPU identity, secure-cloud selection, rate, query time, and cache age at estimate time. It does not expose `uninterruptablePrice` as `on_demand_actual` and does not imply that the provider query is a billing reconciliation.

### Jobs

`TrackedJob` gains:

- `archived_at: datetime | None`
- the persisted cost breakdown supplied by the execution result

Job-store listing uses a typed query contract with:

- lifecycle status
- `since` and `before` UTC timestamps
- `older_than` duration relative to `created_at`
- archived inclusion

The default list excludes archived jobs. `JobResponse.archived_at` exposes archive state for filtered lists and dashboard rows. Archiving is a persisted state change and does not delete plans, outputs, inputs, or cost data; archive tests must prove the record and all associated plan, output, input, and cost data remain available.

### Workers

`RegisteredWorker` gains a bounded `error_history` containing sanitized entries with:

- UTC timestamp
- error message
- consecutive-failure count at the time of the error

The history is capped at 10 entries. A successful health probe resets current failure state and clears `last_error` without deleting the history. Re-registration resets the current lifecycle state (`status`, `consecutive_failures`, `last_error`, and `booting_since`) while retaining the bounded history as an operational breadcrumb.

### Traceability

`VersionResponse` contains only build identity:

- `version`
- `sha`
- `build_time`
- `branch`
- `dirty`
- `image`
- `registry`

Unset build metadata is represented as an explicit unknown/null value, not inferred from the filesystem or arbitrary environment values. The request-ID middleware returns the received or generated ID in the `x-request-id` response header. Public responses do not include credentials, internal paths, or unrelated environment configuration.

### Voice selection

`EpubRequest` and `AudioRequest` accept an optional default `voice`. EPUB requests additionally accept normalized inclusive chapter ranges mapping to voices. The external forms are:

- CLI `--voice NAME`
- repeatable CLI `--voice-map START-END:NAME`
- strict API fields with no unknown-field acceptance

Voice names are case-insensitive at the boundary and canonicalized against the advertised TTS capability metadata. The normalized map is non-empty, one-based, inclusive, non-overlapping, and must reference the readable EPUB chapter set. A map may leave chapters uncovered only when a default voice is supplied; otherwise every chapter must be covered.

`EpubRequest` stores `voice: str | None` and a normalized immutable `voice_map` of inclusive chapter ranges; `AudioRequest` stores only `voice: str | None`. API and CLI inputs are converted to these domain fields before plan compilation, and memory/Redis request serialization round-trips the same canonical values. A submission preflight discovers the EPUB chapter count and receives worker-ID/capability records before creating or persisting a job/plan; it rejects unsupported or non-joint voice sets before persistence. CLI grammar is validated before upload; when EPUB discovery requires an upload, the input is temporary, never attached to a job during preview, atomically promoted on successful submission, and deleted on rejected, failed, or timed-out preflight. The planner validates that one eligible TTS worker record `(worker_id, capabilities)` advertises the complete selected voice set in `WorkerCapabilities.metadata["speakers"]`; dispatcher selection only enforces this already-selected worker and never performs the first validation after persistence. The canonical plan payload keys are `voice` and `voice_map`; the Qwen worker maps them to its model-level `speaker` argument. The selected worker resolves the voice for each input chunk. Audio jobs may use one voice for the full request but reject chapter-range maps.

## API behavior

### Read-only routes

- `GET /jobs` accepts status, creation-window, stale-age, and archived-inclusion filters.
- `GET /jobs/{job_id}` remains the authoritative job detail response.
- `GET /jobs/{job_id}/cost` returns the structured cost explanation and per-step breakdown.
- `GET /cost?window=24h|7d|30d|all` returns aggregate estimated cost, job count, and unknown-cost count.
- `GET /version` returns `VersionResponse`.
- `GET /workers` includes a sanitized current-error and bounded error-history projection while omitting worker endpoints from the public response.
- `GET /capabilities` returns an allowlisted public capability projection and never returns arbitrary worker metadata, endpoints, credentials, or provider request details.

Read-only routes remain unauthenticated, expose only sanitized operational data, and do not accept arbitrary filesystem paths. Worker diagnostics never expose raw tracebacks, credentials, internal endpoints, or provider request details. Sanitization trims and bounds messages, removes traceback lines, bearer/token-like values, URLs and host:port endpoint text, and provider request IDs; tests cover anonymous and registration-token callers. Worker registration responses expose only worker identity/status, and capability responses allowlist worker type, languages, formats, limits, and canonical TTS speaker names while dropping arbitrary metadata and endpoint/provider fields.

`POST /jobs:preview` is the non-job voice preflight. It may consume a temporary input returned by `POST /inputs`, discovers chapter count and jointly capable worker records, creates no job or persisted plan, and leaves the input temporary until successful `POST /jobs` promotion. `DELETE /inputs/{input_id}` is authenticated, idempotent, and removes only an unpromoted temporary input.

### Admin routes

All `/admin/*` routes require a matching `Authorization: Bearer <ACHERON_ADMIN_TOKEN>` header. Server settings map the flat `ACHERON_ADMIN_TOKEN` environment variable to the nested admin-token setting; structured configuration retains precedence. Registration tokens are rejected for these routes, and open-registration mode has no effect.

- `POST /admin/jobs/{job_id}/mark-failed`
- `POST /admin/jobs/reap-stale`
- `POST /admin/jobs/{job_id}/archive`
- `POST /admin/cleanup`

`mark-failed` and `reap-stale` transition only non-terminal jobs. A current in-process execution task is never reaped or marked failed by an administrative request. After an orchestrator restart, persisted `RUNNING` jobs with no active task are eligible for reaping once older than the requested threshold. Reaping adds a sanitized job-level error containing the supplied reason and persists the terminal state.

`archive` is idempotent for an already archived job and preserves all job data. Cleanup candidates are terminal jobs selected by status-specific retention windows; archived terminal jobs remain eligible under those windows and are shown as archived in previews. Cleanup previews return exact job IDs, paths, and reclaimable bytes. The operation deletes only after an explicit apply request and re-evaluates eligibility immediately before deletion while holding the same atomically-created per-job lifecycle lock used by active execution/reaping. Shared uploaded-input identity is protected by an input-level reference lock/refcount spanning submission/promotion and cleanup; cleanup rechecks references while holding that lock. It refuses active jobs and rejects paths that escape the configured data directory. Uploaded inputs are deleted only when no retained job references them. Apply reports per-job deletion failures with relative paths and deleted-byte counts without claiming those jobs were deleted; retrying the same apply is idempotent for already-deleted paths and records.

All admin failures use the shared structured error response `{type, message, remediation}` with sanitized values. Authorization failures distinguish unavailable configuration (`503`) from missing or invalid credentials (`401`); archive, reap, and cleanup failures use the same shape. A dependency/exception-handling seam around the admin router records exactly one failure audit event for authorization, validation, and route exceptions before a response is returned, with the route action identity, request ID, reason when available, affected IDs/count, and `result="failure"`; successful mutations and partial cleanup results emit one corresponding success/failure event without duplicates.

## CLI behavior

The client reads `ACHERON_ADMIN_TOKEN` separately from `ACHERON_REGISTRATION_TOKEN`.

The CLI exposes:

```text
acheron jobs [--since DURATION] [--before ISO] [--status STATUS]
              [--older-than DURATION] [--include-archived]
acheron job archive ID...
acheron job cost ID --explain
acheron admin reap-stuck --older-than DURATION --reason TEXT
acheron cleanup --keep-successful DURATION --keep-failed DURATION [--apply]
acheron version
```

Cleanup is preview-only unless `--apply` is supplied. The preview reports the candidate jobs and reclaimable size. Admin commands fail clearly when `ACHERON_ADMIN_TOKEN` is absent. Voice grammar is parsed and validated before upload or any submission request. Capability/chapter validation occurs through the temporary-input non-job preflight before job creation; temporary inputs are promoted only on successful submission and are deleted on all rejected, failed, or timed-out paths.

Every completed request prints `request_id=<id>` to stderr. HTTP status errors include a sanitized attempted request URL with credentials, query strings, and fragments removed, allowing operators to distinguish a missing job from a stale `ACHERON_URL`. Cost explanation output identifies the estimate basis, GPU, secure-cloud setting, rate, query time, cache age, and any unknown fields. Status, job-detail, and list output use the same execution-time-estimate label and render unknown totals/counts explicitly rather than `$0.00` or a free-usage implication.

## Dashboard behavior

The dashboard continues using its existing HTMX partial model and adds:

- a stuck-only job filter and older-than control;
- cost window controls for 24 hours, 7 days, 30 days, and all jobs;
- a cost aggregate footer explicitly labeled as estimated, containing estimated total, job count, and unknown-cost count;
- estimated/unknown labeling in job rows and job-detail cost displays;
- explanatory cost-basis tooltips;
- GPU type, secure-cloud, rate, query timestamp, and cache-age details for cost rows;
- current worker error and bounded error history;
- deployed version and short SHA in the header.

The dashboard does not gain destructive admin controls. Job rows remain safe to render, and any output links continue through the existing allowlisted output route.

## Error handling and invariants

- Public errors never expose credentials, internal URLs, tracebacks, or arbitrary filesystem paths; source-resolution and missing-input errors use sanitized public messages with remediation while retaining detailed paths only in internal logs.
- Admin authorization is independent from registration authorization.
- Reaping never deletes data and never touches an active in-process task.
- Cleanup never operates on `PENDING` or `RUNNING` jobs.
- Cleanup previews the same policy that apply mode will execute; apply mode rechecks candidates while holding the per-job lifecycle lock and reports partial failures without losing retryability.
- Cleanup stores and retained-job references use one normalized data-directory-relative input identity from upload through submission and deletion, protected by a shared input-level reference lock/refcount.
- All deletion remains below the configured data directory and rejects symlink escapes, including symlink swaps between eligibility and deletion through descriptor-relative no-follow operations or an equivalent atomic containment mechanism.
- Worker error history is sanitized and capped at 10 entries; a bounded tombstone/history record survives health removal so re-registration can reset lifecycle state without losing the breadcrumb.
- Unknown cost remains visibly unknown; zero/stub cost is never presented as free measured usage.
- Voice ranges are validated before plan persistence or execution.
- A voice map is rejected unless one TTS worker jointly supports all selected voices; the selected worker ID is carried through the internal plan/dispatch seam and cannot be replaced by first-match selection.
- Memory and Redis stores round-trip the same logical job and worker records.
- Lifecycle and cost timestamps are timezone-aware UTC values.
- UX verification accepts `PASS` only when the story evidence commit matches the current implementation/evidence commit; stale metadata is not sufficient.

## Stage completion criteria

### Stage 1 — Cost truth

`MAINT-014`, `MAINT-015`, `OPS-005`, `OPS-031`, and `MAINT-002` are complete when worker estimates preserve rate metadata and cache age, zero/stub pricing is distinguishable from static pricing, cost explanations work through API/client/CLI/dashboard, and the dashboard provides time-window aggregates.

### Stage 2 — Recovery administration

`MAINT-001`, `MAINT-008`, `MAINT-010`, `MAINT-011`, `MAINT-012`, and `OPS-012` are complete when operators can filter stale jobs, safely reap jobs left running after restart, archive records, preview and apply retention cleanup, observe disk-pressure warnings, see worker error history after recovery, and use status/time-window/archive filters from the CLI and dashboard.

### Stage 3 — Traceability and deployed-version visibility

`OPS-022`, `MAINT-013`, and `MAINT-016` are complete when status errors identify the attempted URL, every CLI request exposes the response request ID, `GET /version` returns build identity, and the dashboard header renders the deployed version and SHA.

### Stage 4 — Job-level voice selection

`OPS-028` is complete when a valid default voice or chapter voice map reaches the plan, planner validation rejects unsupported or ambiguous requests before execution, one jointly capable TTS worker is selected, and the worker applies the normalized voice to each chunk.

## Testing and verification

Focused tests precede each implementation stage:

- Worker pricing tests for measured, cached, static, stub, unknown, GPU metadata, secure-cloud metadata, query time, and cache age.
- Core and wire-schema tests for cost breakdowns, archive state, worker error history, version metadata, request IDs, and voice requests.
- Memory and Redis store parity tests for job filtering, archival, deletion, cost data, worker re-registration, and error-history retention.
- Orchestrator tests for stale-job detection, active-task exclusion, admin state transitions, cleanup eligibility, disk-usage warnings, and voice-aware plan compilation.
- API/client tests for admin authorization/failure auditing, read-only filters, cost reports and `GET /cost?window=7d` query binding, archived response mapping, structured errors, `/version`, response request IDs, sanitized source-path errors, and strict voice request shapes.
- CLI tests for execution-time estimated cost labels and unknown values in cost/status/detail displays, time/status/archive filters, cleanup preview/apply, stale-job reaping, request-ID output, URL diagnostics across connection/timeout/status/follow-up paths, version output, and voice options before upload.
- Dashboard tests for estimated-cost windows and unknown counts, aggregate totals, tooltips, GPU/rate/cache-age details, archived rows, stuck filters, sanitized worker history, and version headers.
- Integration journeys for cost outage and GPU changes, orphaned jobs after restart, cleanup preview/apply with normalized input identity and symlink-swap race coverage, worker recovery history, wrong-base-URL diagnostics, sanitized source errors, and voice-mapped EPUB preflight/submission.
- UX evidence updates only after behavior passes and each story’s `just ux-verify` command succeeds.

The final verification sequence is:

```bash
just lint-strict
just type-check
just test
just validate
just ux-validate
just first-run
just ux-verify MAINT-014
just ux-verify MAINT-015
just ux-verify OPS-005
just ux-verify OPS-031
just ux-verify MAINT-002
just ux-verify MAINT-001
just ux-verify MAINT-008
just ux-verify MAINT-010
just ux-verify MAINT-011
just ux-verify MAINT-012
just ux-verify OPS-012
just ux-verify OPS-022
just ux-verify MAINT-013
just ux-verify MAINT-016
just ux-verify OPS-028
```

The RunPod bootstrap journey is required only if implementation changes the exercised RunPod worker image or registration path. The final implementation review must include an independent correctness and documentation-staleness review.

## Non-goals

- Exact provider invoice reconciliation.
- A general log aggregation or billing system.
- A full admin role, JWT, or multi-operator identity system.
- Registration-token rotation or token history.
- Certificate rotation, certificate expiry monitoring, or production SAN management.
- Multi-worker voice fan-out or branch-aware executor DAGs.
- Automatic cleanup without an explicit operator action.
- Destructive dashboard controls.
