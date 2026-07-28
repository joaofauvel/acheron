# Phase 4B Readiness Experience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist worker BOOTING start times, expose trustworthy elapsed/timeout data, show a live dashboard countdown, and return informational TTS cold-start warnings without gating job submission.

**Architecture:** `RegisteredWorker.booting_since` is the single persisted source of truth. Worker stores set it only when a worker enters `BOOTING` and clear it on every transition out of `BOOTING`, unregister, or re-registration. `HealthMonitor` uses the persisted wall-clock timestamp for a 600-second timeout and one 540-second warning. The worker API computes elapsed time at response time, while the dashboard advances the server snapshot locally once per second. Job submission accepts the job first, then derives informational warnings from the registered TTS fleet; the response and CLI carry those warnings without changing success or error semantics.

**Tech stack:** Python 3.14, FastAPI, Pydantic, Redis, Jinja2, HTMX, dependency-free browser JavaScript, pytest/pytest-asyncio with in-process ASGI transports.

## Global constraints

- Use strict TDD: add and run the behavior test red before adding the production behavior it exercises, then run it green. In particular, Task 1 starts with failing in-memory lifecycle tests before adding `RegisteredWorker.booting_since` or store implementation, and Task 3 starts with failing `JobResponse.warnings` schema/route tests before adding that field.
- Add no dependencies, no type ignores, and no production or test compatibility paths unrelated to this design.
- Keep tests in-process. Redis round-trip and concurrency tests may use the existing `testcontainers` fixture; do not start a second application server.
- Use `time.time()` for persisted `booting_since` so elapsed time survives process restart and Redis round trips. Never expose a client-supplied elapsed or timestamp field.
- Keep `_BOOTING_TIMEOUT_SECONDS` exactly `600.0` and warn at exactly `540.0` seconds (90%). Emit at most one near-timeout warning per BOOTING lifecycle, and clear warning bookkeeping whenever the worker leaves BOOTING.
- Preserve the existing `provider BOOTING timeout exceeded` error, OFFLINE transition, failure counting, worker removal, status authentication, TLS, non-root, Compose, and simulator behavior.
- Do not change `src/acheron/shell/api/routes/partials.py` or the OPS-007 HTML readiness contract. Do not gate submission on worker status or add unrelated submission validation.
- Warnings must identify every affected worker ID and its elapsed time. The approved cold-start guidance is that startup typically takes 30–90 seconds.
- Existing healthy, offline, and error dashboard rows must retain their current badges and error rendering.
- The implementation commit must contain `fixed_in: [pending]` metadata and must leave `verified_in: []`, `last_verified_at: {}`, and `verified_by: ""`. Resolve only `fixed_in` in the separate metadata-only commit; never write `last_verified_at.commit: pending`.

## Execution precondition

Commit this plan separately with `docs(OPS-006,OPS-019,MAINT-009): plan readiness experience`, then ensure the branch and index are clean before Task 1 begins. The plan commit is not part of the implementation or metadata commits.

```bash
git log -1 --format=%s
git status --short
git diff --cached --quiet
```

Expected: the first command prints the plan commit subject and the other commands print nothing. Do not begin implementation while the plan is unstaged or while unrelated changes are present.

## Interfaces and invariants

### Worker state

Add this field to `RegisteredWorker` without changing existing constructor defaults:

```python
booting_since: float | None = None
```

The store-facing status transition contract remains:

```python
async def set_worker_status(
    self,
    worker_id: str,
    status: WorkerStatus,
    last_error: str | None,
) -> None: ...
```

Its behavior is:

- `status == WorkerStatus.BOOTING` and current status is not BOOTING (or has no timestamp): set `booting_since = time.time()`.
- `status == WorkerStatus.BOOTING` while already BOOTING: preserve the existing timestamp.
- Any other status: set `booting_since = None`.
- `register()` always creates a healthy worker with `booting_since=None`, including re-registration.
- `record_health_success()`, `unregister()`, and OFFLINE transitions clear/remove the timestamp.

A persisted BOOTING record must always have a non-empty timestamp. Redis deserialization raises the existing chained `CacheCorruptedError` for a BOOTING hash missing `booting_since`; a missing field on a healthy/non-BOOTING hash normalizes to `None` so existing healthy hashes remain readable. In-memory direct fixtures may defensively omit such a worker from warning output, but stores must never create that state.

### Redis atomic transition protocol

Do not perform an external read followed by a transaction. Add `eval` to the typed `_RedisAwaitable` protocol in `src/acheron/shell/stores/redis.py` with this exact call surface and add an awaitable probe for `("eval", ("return 1", 0), {})`:

```python
async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> object: ...
```

`set_worker_status()` uses one Redis Lua script (no new dependency), with `KEYS[1]` as the worker hash and `ARGV[1]` status, `ARGV[2]` last error (empty string for `None`), and `ARGV[3]` current `time.time()`:

```lua
local key = KEYS[1]
if redis.call("EXISTS", key) == 0 then
  return 0
end
local current_status = redis.call("HGET", key, "status") or "healthy"
local current_since = redis.call("HGET", key, "booting_since") or ""
local next_since = ""
if ARGV[1] == "booting" then
  if current_status == "booting" and current_since ~= "" then
    next_since = current_since
  else
    next_since = ARGV[3]
  end
end
redis.call("HSET", key,
  "status", ARGV[1],
  "last_error", ARGV[2],
  "booting_since", next_since)
return 1
```

The script atomically reads the current status/timestamp and writes status, error, and timestamp. The Python method awaits `self._redis.eval(script, 1, key, status.value, last_error or "", str(time.time()))`; it must not call `exists()`, `hget()`, or `hset()` around that operation. `record_health_success()` may use one transaction to set HEALTHY, reset the counters, clear the error, and clear `booting_since`.

### Worker API

Extend `WorkerResponse` with:

```python
booting_elapsed_seconds: float | None = None
booting_timeout_seconds: float = 600.0
```

`GET /workers` computes `booting_elapsed_seconds` as `max(0.0, time.time() - w.booting_since)` only for a BOOTING worker with a persisted timestamp; all other statuses return `None`. `booting_timeout_seconds` is always `600.0`. `POST /workers` returns the healthy defaults. Never read either field from a request.

### Submission response and warning format

Extend `JobResponse` with:

```python
warnings: list[str] = Field(default_factory=list)
```

`POST /jobs` accepts and persists the job exactly as it does now, then inspects registered workers. A helper with this interface produces deterministic warnings:

```python
def _booting_tts_warnings(
    workers: tuple[RegisteredWorker, ...],
    *,
    now: float,
) -> list[str]: ...
```

Filter to `WorkerType.TTS` and `WorkerStatus.BOOTING`, sort by worker ID, and render one warning containing all affected IDs and elapsed seconds:

```text
BOOTING TTS workers: tts-1 (3s elapsed), tts-2 (12s elapsed); cold start typically takes 30–90 seconds.
```

Elapsed values are non-negative whole seconds: compute `floor(max(0.0, now - booting_since))`, including clamping a wall-clock move backwards to zero. A BOOTING worker without a timestamp violates the store invariant and contributes no warning in the defensive helper path. If no TTS worker is BOOTING, return `[]`. Healthy TTS workers, non-TTS BOOTING workers, and an empty fleet produce no warning. Warning collection is informational and must not reject, delay, or otherwise gate a successfully accepted job; if post-acceptance fleet inspection fails, log the inspection failure and return the successful response with `warnings=[]`.

## File map

### Modify

- `src/acheron/shell/registry.py` — add persisted `booting_since` to `RegisteredWorker`.
- `src/acheron/shell/stores/base.py` — document the status-transition clearing/creation contract in `WorkerStore`.
- `src/acheron/shell/stores/memory.py` — initialize, preserve, and clear the timestamp in the in-memory store.
- `src/acheron/shell/stores/redis.py` — serialize/deserialize `booting_since`, expose typed `eval`, and implement the Lua transition.
- `src/acheron/shell/health.py` — remove timestamp as monitor-only state, consume persisted time, warn once at 540 seconds, and retain timeout behavior.
- `src/acheron/core/schemas.py` — add worker elapsed/timeout fields and, in Task 3 only, `JobResponse.warnings`.
- `src/acheron/shell/api/routes/workers.py` — compute worker elapsed time at response time and expose the fixed timeout.
- `src/acheron/shell/api/routes/jobs.py` — collect post-acceptance TTS BOOTING warnings and map them only onto submission responses.
- `src/acheron/cli.py` — print returned warnings after normal submission lines in yellow without changing exit status.
- `dashboard/app.py` — expose the browser-independent progress-formatting helper to Jinja.
- `dashboard/templates/partials/workers.html` — render BOOTING elapsed text and a timeout-bounded progress element with data attributes.
- `dashboard/templates/index.html` — add progress styling and one global one-second timer that survives HTMX swaps.
- `tests/shell/stores/test_memory_worker_store.py` — cover in-memory timestamp lifecycle and same-ID re-registration.
- `tests/shell/stores/test_redis_worker_store.py` — cover Redis timestamp round trips, Lua transitions, and concurrent invariants.
- `tests/shell/test_health_monitor.py` — cover persisted elapsed timeout behavior and one near-timeout warning per lifecycle.
- `tests/core/test_schemas.py` — cover worker response defaults in Task 2 and warning defaults/serialization in Task 3.
- `tests/shell/api/test_workers.py` — cover computed elapsed/timeout fields and preserve auth/error-scrubbing behavior.
- `tests/shell/api/test_jobs.py` — cover warning selection, deterministic IDs/elapsed values, backward-clock clamp, and non-gating submission.
- `tests/shell/test_cli.py` — cover warning output and successful exit status.
- `dashboard/tests/test_dashboard.py` — cover countdown/progress markup, timer wiring, and unchanged healthy/offline/error rows.
- `docs/ux_review/ops.md` — refresh OPS-006/OPS-019 metadata, prose, `last_updated_date`, and aggregate grade/open-story summary.
- `docs/ux_review/maint.md` — refresh MAINT-009 metadata, prose, `last_updated_date`, and aggregate grade/open-story summary.

### Verify only

- `src/acheron/api_client.py` — no method signature change is expected; verify its existing `JobResponse.model_validate()` carries `warnings` through `submit_job()`.

### New files

- `dashboard/booting_progress.py` — browser-independent, typed elapsed formatting and one-second clamped progress update helper used by dashboard rendering and tests.
- `dashboard/tests/test_booting_progress.py` — behavior tests for non-negative whole-second formatting, wall-clock clamp, timeout clamp, and percentage/update values.
- `tests/test_api_client.py` — in-process `respx` coverage proving a `POST /jobs` JSON warning round-trips through `AcheronClient.submit_job()` into `JobResponse.warnings`.

## Ordered implementation tasks

### Task 1: Persist BOOTING timestamps and move timeout bookkeeping into `HealthMonitor`

- [ ] **1.1 Write and run failing in-memory lifecycle tests before production changes.**
  - File: `tests/shell/stores/test_memory_worker_store.py`
  - Changes: First add tests that assert registration starts with no timestamp; monkeypatch `acheron.shell.stores.memory.time.time` to a known value, enter BOOTING, assert that value is stored; enter BOOTING again at a later value and assert the original timestamp remains; assert HEALTHY and OFFLINE clear it; unregister removes the worker; and same-ID re-registration resets status, error, failures, and timestamp. Do not add `RegisteredWorker.booting_since` or store implementation before this red run.
  - Acceptance: Run `uv run pytest --no-cov tests/shell/stores/test_memory_worker_store.py -q`. The new assertions fail against the current implementation because the production field and transitions do not yet exist.

- [ ] **1.2 Add the domain field after the red test.**
  - File: `src/acheron/shell/registry.py`
  - Changes: Add `booting_since: float | None = None` to `RegisteredWorker`; keep it optional/defaulted so direct healthy test construction remains valid.
  - Acceptance: The focused memory command still fails on transition assertions, proving the store behavior has not been hidden by the field alone.

- [ ] **1.3 Implement transition-aware in-memory storage.**
  - File: `src/acheron/shell/stores/memory.py`
  - Changes: Initialize `booting_since=None` in `register()`. In `set_worker_status()`, preserve an existing timestamp for repeated BOOTING updates, call `time.time()` only on first BOOTING entry, and clear it for HEALTHY, OFFLINE, or every other non-BOOTING status. `record_health_success()` explicitly clears it. Leave unregister removal unchanged.
  - Acceptance: The focused memory command passes, including same-ID re-registration and existing status/error behavior.

- [ ] **1.4 Write and run failing Redis round-trip, Lua-transition, and concurrency tests.**
  - File: `tests/shell/stores/test_redis_worker_store.py`
  - Changes: Extend registration/get assertions to require `booting_since is None`. Add a round trip that sets BOOTING, reads a non-`None` timestamp, repeats BOOTING at a later patched time and asserts it is unchanged, then sets HEALTHY/OFFLINE and asserts `None`; assert re-registration resets the lifecycle. Add a corruption fixture that writes a BOOTING hash without `booting_since` and assert `get()` raises the existing chained `CacheCorruptedError`. Add concurrent `asyncio.gather()` transitions with distinct clock values and assert the final hash always satisfies the invariant: BOOTING implies a non-empty timestamp, while every non-BOOTING status implies an empty timestamp. Keep the existing `redis_url` fixture and test actual Redis, not a fake.
  - Acceptance: Run `uv run pytest --no-cov tests/shell/stores/test_redis_worker_store.py -q`. The new assertions fail before Redis serialization, the Lua call, and atomic transition handling exist. If Docker is unavailable, record that environmental failure and do not replace the test with a fake Redis test.

- [ ] **1.5 Implement Redis persistence and the atomic Lua transition.**
  - File: `src/acheron/shell/stores/redis.py`
  - Changes: Add `booting_since` to `_worker_fields()` as empty for healthy registration; parse empty as `None` and float otherwise; raise chained `CacheCorruptedError` for BOOTING without a timestamp. Add the typed `eval` protocol/probe exactly as specified above. Implement `set_worker_status()` solely through the exact Lua script and typed call, passing status/error/current wall clock as ARGV. Do not perform an external read plus transaction. Keep `record_health_success()` atomic and clear the field.
  - Acceptance: The Redis command from 1.4 passes, including the missing-timestamp chained `CacheCorruptedError`, round trips, re-registration, and concurrent transition invariants. Confirm the field through `get()`/Redis round trip rather than mocked command calls.

- [ ] **1.6 Update the WorkerStore contract documentation.**
  - File: `src/acheron/shell/stores/base.py`
  - Changes: Document that BOOTING entry starts/preserves the persisted timestamp and every non-BOOTING transition and health success clears it; do not change the public signature or add a configuration knob.
  - Acceptance: `uv run ruff check src/acheron/shell/stores/base.py` passes and both stores still implement the abstract interface.

- [ ] **1.7 Write and run failing HealthMonitor tests against persisted wall-clock time.**
  - File: `tests/shell/test_health_monitor.py`
  - Changes: Replace assertions against monitor-private `_booting_since` with `RegisteredWorker.booting_since`. With a monkeypatched `acheron.shell.health.time.time`, assert first BOOTING failure persists `0.0`, `539.9` remains BOOTING, and `600.0` preserves `provider BOOTING timeout exceeded`, OFFLINE status, and failure counting. At `540.0`, invoke multiple BOOTING checks and assert exactly one warning containing the worker ID, threshold, and timeout context. Assert recovery clears warning bookkeeping and a same-ID re-registration/new BOOTING timestamp permits one fresh warning. Keep existing removal, provider transition, concurrent-check, and timeout tests.
  - Acceptance: Run `uv run pytest --no-cov tests/shell/test_health_monitor.py -q`. New assertions are red because the monitor still owns monotonic/private timestamp state and has no 540-second warning.

- [ ] **1.8 Implement persisted timeout and lifecycle-keyed warning behavior.**
  - File: `src/acheron/shell/health.py`
  - Changes: Keep the 600-second constant and add the exact 540-second threshold. Replace monitor-owned timestamp state with a small warning-emitted set keyed by `(worker_id, persisted_booting_since)`, so a new timestamp is a new lifecycle even when the worker ID is reused. Read the persisted timestamp after BOOTING is recorded, calculate `max(0.0, time.time() - booting_since)`, warn once at 540 seconds, continue BOOTING until 600, then retain the exact existing timeout error and OFFLINE/failure behavior. Clear keys on HEALTHY, OFFLINE, provider-state changes, unregister, and any transition out of BOOTING; do not weaken exception handling or remove concurrency.
  - Acceptance: The HealthMonitor command from 1.7 passes, including one warning per lifecycle and all existing timeout/removal/re-registration behavior.

- [ ] **1.9 Run the combined persistence red/green gate.**
  - Files: registry, store base/memory/Redis, health monitor, and their tests.
  - Acceptance: Run `uv run pytest --no-cov tests/shell/stores/test_memory_worker_store.py tests/shell/stores/test_redis_worker_store.py tests/shell/test_health_monitor.py -q`. It passes with actual Redis round-trip/concurrency coverage and no OPS-007 HTML endpoint changes.

### Task 2: Expose worker timing data and render the live dashboard countdown

- [ ] **2.1 Write and run failing worker-schema tests only.**
  - File: `tests/core/test_schemas.py`
  - Changes: Assert a default `WorkerResponse` has `booting_elapsed_seconds is None` and `booting_timeout_seconds == 600.0`; keep existing enum/cost-basis assertions. Do not add `JobResponse.warnings` assertions in Task 2.
  - Acceptance: Run `uv run pytest --no-cov tests/core/test_schemas.py -q`. The new worker-field assertions are red until Task 2.2.

- [ ] **2.2 Add worker response fields with defaults.**
  - File: `src/acheron/core/schemas.py`
  - Changes: Add only `booting_elapsed_seconds` and `booting_timeout_seconds` with the exact defaults in the interface section; do not add `JobResponse.warnings` here.
  - Acceptance: The schema command from 2.1 passes and `uv run mypy src/acheron/core/schemas.py tests/core/test_schemas.py` reports no new errors.

- [ ] **2.3 Write and run failing worker-route timing tests.**
  - File: `tests/shell/api/test_workers.py`
  - Changes: Register and set a worker BOOTING, patch route `time.time()` to a known value, and assert GET returns persisted elapsed and `600.0`. Assert healthy/OFFLINE return `null` elapsed and POST returns healthy/null/600 defaults. Extend auth tests to prove timing remains visible while `last_error` scrubbing is unchanged.
  - Acceptance: Run `uv run pytest --no-cov tests/shell/api/test_workers.py -q`; new timing assertions are red before route implementation.

- [ ] **2.4 Implement response-time elapsed computation.**
  - File: `src/acheron/shell/api/routes/workers.py`
  - Changes: Add a typed helper that returns `max(0.0, now - booting_since)` only for BOOTING workers with a timestamp; use one `time.time()` snapshot in list responses, fixed `600.0`, and healthy defaults in registration. Preserve authorization and never accept timing fields from requests.
  - Acceptance: The worker-route command from 2.3 passes, including auth and error-sanitization regressions.

- [ ] **2.5 Write failing dashboard markup and wiring tests.**
  - File: `dashboard/tests/test_dashboard.py`
  - Changes: Extend the BOOTING fixture with `booting_elapsed_seconds: 182.0` and `booting_timeout_seconds: 600.0`; assert elapsed text, progress max/value/data attributes, and existing error details. Add index assertions only for the timer source and one-second interval wiring. Keep explicit healthy/offline/error assertions and assert those rows do not receive BOOTING progress markup.
  - Acceptance: Run `uv run pytest --no-cov dashboard/tests/test_dashboard.py -q`; markup and timer wiring assertions are red against current templates.

- [ ] **2.6 Implement BOOTING progress markup without altering OPS-007.**
  - File: `dashboard/templates/partials/workers.html`
  - Changes: In the existing status cell, branch only for `w.status == "booting"`; retain the badge and render deterministic elapsed/timeout text plus native `<progress>` `value`, `max`, and stable data attributes. Keep healthy/offline/error rows, error `<details>`, and empty state unchanged.
  - Acceptance: Dashboard tests pass markup assertions while timer behavior remains pending; `git diff -- src/acheron/shell/api/routes/partials.py` is empty.

- [ ] **2.7 Write and run failing browser-independent progress-helper tests.**
  - File: `dashboard/tests/test_booting_progress.py`
  - Changes: Test the helper contract for `format_booting_elapsed()` and `advance_booting_progress()`: negative and fractional inputs become non-negative whole seconds, elapsed values clamp at zero, progress clamps at timeout, and the deterministic label/percentage/update values are correct at 0, 182, and 600 seconds.
  - Acceptance: Run `uv run pytest --no-cov dashboard/tests/test_booting_progress.py -q`; tests are red before the helper exists.

- [ ] **2.8 Implement the deterministic helper and make server markup use it.**
  - Files: `dashboard/booting_progress.py`, `dashboard/app.py`, `dashboard/templates/partials/workers.html`
  - Changes: Add typed, DOM-independent formatting/update functions with the same non-negative whole-second and timeout-clamp semantics as the browser timer. Register the formatter as a Jinja global and call it from the BOOTING branch in `partials/workers.html` so the initial label uses the tested behavior. Preserve Task 2.6's progress attributes, healthy/offline/error rows, and existing template structure. Do not add a dependency or alter orchestrator requests.
  - Acceptance: The helper test command from 2.7 passes and dashboard app construction/type checking remains green.

- [ ] **2.9 Implement one global one-second timer that tolerates HTMX swaps.**
  - File: `dashboard/templates/index.html`
  - Changes: Add concise progress CSS and one dependency-free script. Define a DOM-independent formatting/update helper or invoke the tested deterministic contract, then make `updateBootingProgress()` query the current DOM on every tick, increment BOOTING data by one second, clamp to its timeout, and update elapsed text/progress value/percentage. Start one `setInterval(updateBootingProgress, 1000)`; querying after each swap keeps the `hx-trigger="every 2s"` snapshot authoritative. The test in 2.7 is the behavior check; source assertions in 2.5 are wiring-only.
  - Acceptance: Dashboard tests and helper tests pass; healthy/offline/error/empty rows remain unchanged and the timer has exactly one one-second interval.

- [ ] **2.10 Run the worker API plus dashboard gate.**
  - Files: worker schema/route, dashboard app/helper/templates/tests.
  - Acceptance: Run `uv run pytest --no-cov tests/core/test_schemas.py tests/shell/api/test_workers.py dashboard/tests/test_dashboard.py dashboard/tests/test_booting_progress.py -q`; all pass and `git diff -- src/acheron/shell/api/routes/partials.py` is empty.

### Task 3: Add non-gating TTS BOOTING warnings through API client and CLI

- [ ] **3.1 Write and run failing warning schema, API-client, and route tests before adding the field.**
  - Files: `tests/core/test_schemas.py`, `tests/shell/api/test_jobs.py`, `tests/test_api_client.py`
  - Changes: Add `JobResponse(...).warnings == []`, explicit warning serialization, and response validation tests. Add the in-process `respx` round-trip test for `AcheronClient.submit_job()` returning HTTP 201 with a warning and asserting `JobResponse.warnings` preserves it. Build an in-process app with an `InMemoryWorkerStore`, register a TTS worker, set it BOOTING at a patched clock value, submit a valid EPUB job, and assert HTTP 201/job ID/status unchanged plus one warning containing ID, elapsed whole seconds, and `30–90 seconds`. Cover multiple sorted TTS IDs, healthy TTS, BOOTING ASR, no workers, a backwards wall-clock value clamped to `0s`, a BOOTING worker missing a timestamp being omitted defensively, and a post-acceptance `list_workers()` failure returning successful response with `warnings=[]`. Do not add `JobResponse.warnings` production code before this red run.
  - Acceptance: Run `uv run pytest --no-cov tests/core/test_schemas.py tests/shell/api/test_jobs.py tests/test_api_client.py -q`. Warning schema, route, and round-trip assertions are red because the field and route behavior do not exist.

- [ ] **3.2 Add the warning schema field and route helper after the red test.**
  - Files: `src/acheron/core/schemas.py`, `src/acheron/shell/api/routes/jobs.py`
  - Changes: Add `warnings: list[str] = Field(default_factory=list)` to `JobResponse`. Implement `_booting_tts_warnings()` with the exact interface/filter/sort/format contract above and `floor(max(0.0, now - booting_since))`. After `orch.submit_job()` succeeds, call `orch.list_workers()` only for best-effort warning calculation; isolate/log inspection failures and return `[]`. Let `_tracked_to_response()` accept optional warnings, pass them only from POST, and leave GET/list/resume responses at default empty warnings. Never inspect readiness before submission or change 201/4xx/5xx semantics.
  - Acceptance: The command from 3.1 passes; BOOTING never gates a successfully accepted job. `src/acheron/api_client.py` remains unchanged because its existing model validation carries the new field.

- [ ] **3.3 Write and run failing CLI warning-output tests.**
  - File: `tests/shell/test_cli.py`
  - Changes: Extend successful submit response with a warning and assert yellow `Warning:` output contains ID, elapsed text, and guidance after existing Job submitted/Status/Plan lines while exit code remains 0. Keep a warning-free old payload and all HTTP-error/remediation tests.
  - Acceptance: Run `uv run pytest --no-cov tests/shell/test_cli.py -q`; warning assertion is red before CLI implementation.

- [ ] **3.4 Render warnings without changing CLI exit semantics.**
  - File: `src/acheron/cli.py`
  - Changes: After existing submission lines, iterate `result.warnings` and print each yellow `Warning:` line. Do not call health endpoints, change `_run()`, alter remediation, or raise on warnings.
  - Acceptance: The CLI command from 3.3 passes and existing success/failure paths remain green.

- [ ] **3.5 Run the complete submission-surface gate.**
  - Files: schema, jobs route, API-client boundary (verify-only), CLI, and tests.
  - Acceptance: Run `uv run pytest --no-cov tests/core/test_schemas.py tests/shell/api/test_jobs.py tests/test_api_client.py tests/shell/test_cli.py -q`. Confirm `src/acheron/api_client.py` remains unchanged.

### Task 4: Refresh UX metadata, review every surface, commit, and verify

- [ ] **4.1 Reconcile OPS-006/OPS-019/MAINT-009 documentation before verification.**
  - Files: `docs/ux_review/ops.md`, `docs/ux_review/maint.md`
  - Changes: Update only the three story blocks' final file paths/line ranges, issue/recommendation/verification prose, and required implementation references. Preserve unrelated story blocks and each changed story's ID, severity, discovery channels, user-facing surface, journey stage, related links, and incident/feedback references. Set all three to `status: fixed`, `fixed_in: [pending]`, `verified_in: []`, `last_verified_at: {}`, and `verified_by: ""`. Do not add any `pending` value outside `fixed_in`.
  - Changes: Update each document's frontmatter `last_updated_date` to `2026-07-28` and refresh the currently stale aggregate summaries to match the post-bundle story statuses: OPS becomes `D (7 high + 17 medium-severity open stories)`; MAINT becomes `C (12 high + 4 medium-severity open stories)`. Preserve the existing grade letters, calibration text, and all unrelated story blocks.
  - Acceptance: Run `uv run python -m acheron.ux_review.validate --root docs/ux_review --head "$(git rev-parse HEAD)" --strict`; it passes with all final paths and line ranges valid. Do not run story verification yet; the verification metadata intentionally remains empty.

- [ ] **4.2 Run exact focused/per-surface gates before review.**
  - Changes: Run all affected tests, repository gates, UX validation, and first-run checks. Do not skip Redis or first-run checks.
  - Acceptance: Run:
    ```bash
    uv run pytest --no-cov tests/shell/stores/test_memory_worker_store.py tests/shell/stores/test_redis_worker_store.py tests/shell/test_health_monitor.py -q
    uv run pytest --no-cov tests/core/test_schemas.py tests/shell/api/test_workers.py tests/shell/api/test_jobs.py tests/test_api_client.py tests/shell/test_cli.py -q
    uv run pytest --no-cov dashboard/tests/test_dashboard.py dashboard/tests/test_booting_progress.py -q
    just validate
    just ux-validate
    just first-run --step 3
    ```
    All commands pass; no story verifier is used as post-merge evidence before the implementation exists.

- [ ] **4.3 Run both fresh reviews against the complete working-tree diff before committing.**
  - Changes: In two fresh contexts, review `git diff -- .` including all production, test, dashboard, and documentation changes; do not review only a selected commit or only production files. The correctness review checks one persisted timestamp, Lua atomicity/typed eval surface and concurrent invariant coverage, lifecycle warning keying, wall-clock clamps/formatting, 540/600 behavior, API elapsed computation, sorted all-ID warnings, non-gating 201/CLI semantics, dashboard helper behavior plus interval wiring, auth/error scrubbing, OPS-007 contract, and no dependency/TLS/auth/Compose/non-root changes. The documentation-staleness review checks all three story prose, paths/line ranges, aggregate summaries, `last_updated_date`, and metadata state; require `fixed_in: [pending]` as the only pending value and empty verification fields.
  - Acceptance: Resolve every blocker/finding in the relevant working-tree file, rerun affected focused tests and `just validate`/`just ux-validate`, and repeat the affected fresh review. Do not create the implementation commit until both reviews are clean.

- [ ] **4.4 Inspect scope and create the implementation commit.**
  - Files: all production/test/dashboard/helper files listed in Tasks 1–3 plus `docs/ux_review/ops.md` and `docs/ux_review/maint.md`; do not stage this plan file.
  - Changes: Confirm `src/acheron/shell/api/routes/partials.py` is untouched and no dependency, TLS, auth, Compose, non-root, or submission-gating change is present. Confirm the implementation metadata contains `fixed_in: [pending]`, `verified_in: []`, `last_verified_at: {}`, and `verified_by: ""`; only `fixed_in` is pending.
  - Acceptance: Run:
    ```bash
    git diff --check
    git diff --stat
    git status --short
    git add src/acheron/shell/registry.py src/acheron/shell/stores/base.py src/acheron/shell/stores/memory.py src/acheron/shell/stores/redis.py src/acheron/shell/health.py src/acheron/core/schemas.py src/acheron/shell/api/routes/workers.py src/acheron/shell/api/routes/jobs.py src/acheron/cli.py dashboard/app.py dashboard/booting_progress.py dashboard/templates/partials/workers.html dashboard/templates/index.html tests/shell/stores/test_memory_worker_store.py tests/shell/stores/test_redis_worker_store.py tests/shell/test_health_monitor.py tests/core/test_schemas.py tests/shell/api/test_workers.py tests/shell/api/test_jobs.py tests/test_api_client.py tests/shell/test_cli.py dashboard/tests/test_dashboard.py dashboard/tests/test_booting_progress.py docs/ux_review/ops.md docs/ux_review/maint.md
    git diff --cached --name-only
    git commit -m "fix(OPS-006,OPS-019,MAINT-009): surface worker booting readiness"
    ```
    The staged list contains exactly the intended implementation/test/dashboard/helper/metadata files and not this plan. The implementation commit is immutable after creation.

- [ ] **4.5 Create the separate story-scoped immutable metadata commit.**
  - Files: `docs/ux_review/ops.md`, `docs/ux_review/maint.md`
  - Changes: Capture `IMPLEMENTATION_SHA="$(git rev-parse HEAD)"` and replace only each target story's `fixed_in: [pending]` with `fixed_in: ["$IMPLEMENTATION_SHA"]`. Use a story-scoped block script bounded by each `## OPS-006`, `## OPS-019`, and `## MAINT-009` heading; do not touch unrelated stories, aggregate summaries, `last_updated_date`, `verified_in`, `last_verified_at`, or `verified_by`. Do not amend the implementation commit and never point `fixed_in` at this metadata commit.
  - Acceptance: Run:
    ```bash
    IMPLEMENTATION_SHA="$(git rev-parse HEAD)"
    uv run python - "$IMPLEMENTATION_SHA" <<'PY'
    from pathlib import Path
    import sys

    implementation_sha = sys.argv[1]
    targets = (
        (Path("docs/ux_review/ops.md"), ("OPS-006", "OPS-019")),
        (Path("docs/ux_review/maint.md"), ("MAINT-009",)),
    )
    for path, story_ids in targets:
        text = path.read_text(encoding="utf-8")
        for story_id in story_ids:
            start = text.index(f"## {story_id} ")
            end = text.find("\n## ", start + 1)
            end = len(text) if end < 0 else end
            block = text[start:end]
            if block.count("fixed_in: [pending]") != 1:
                raise SystemExit(f"expected one pending fixed_in in {story_id}")
            block = block.replace("fixed_in: [pending]", f"fixed_in: [{implementation_sha}]", 1)
            text = text[:start] + block + text[end:]
        path.write_text(text, encoding="utf-8")
    PY
    git diff -- docs/ux_review/ops.md docs/ux_review/maint.md
    git diff --check
    git add docs/ux_review/ops.md docs/ux_review/maint.md
    git commit -m "docs(ux-review): record Phase 4B readiness fix"
    git show --format=fuller --stat HEAD
    ```
    The metadata commit is separate, contains only the two target documentation files, resolves all three `fixed_in` values to the prior implementation hash, and leaves verification fields empty.

- [ ] **4.6 Run post-metadata gates and verify the clean index.**
  - Changes: Run focused behavior tests, full repository gate, UX validator, dashboard first-run step, and all three story verifiers after the metadata-only commit. Treat verifier output as post-merge verification evidence; do not rewrite the immutable implementation commit or invent pending verification metadata.
  - Acceptance: Run:
    ```bash
    uv run pytest --no-cov tests/shell/stores/test_memory_worker_store.py tests/shell/stores/test_redis_worker_store.py tests/shell/test_health_monitor.py tests/core/test_schemas.py tests/shell/api/test_workers.py tests/shell/api/test_jobs.py tests/test_api_client.py tests/shell/test_cli.py dashboard/tests/test_dashboard.py dashboard/tests/test_booting_progress.py -q
    just validate
    just ux-validate
    just first-run --step 3
    just ux-verify OPS-006
    just ux-verify OPS-019
    just ux-verify MAINT-009
    git diff HEAD^ -- docs/ux_review/ops.md docs/ux_review/maint.md
    git status --short
    git diff --cached --quiet
    ```
    All commands pass, the final commit diff is metadata-only, the three verifiers report their expected post-merge state, and no staged files remain.

## Dependencies

- Task 1.1 must be red before Task 1.2 adds the domain field; Task 1.3 follows the field and memory tests.
- Task 1.4 must be red before Task 1.5 adds Redis serialization/Lua; Task 1.7 must be red before Task 1.8 adds monitor behavior.
- Task 1 must complete before Task 2.3 because the worker route reads persisted time and before Task 3.1 because warnings use the same field.
- Task 2.1/2.2 cover worker fields only; `JobResponse.warnings` tests and implementation are exclusively Task 3.1/3.2.
- Task 2.7 must be red before Task 2.8 adds the dashboard helper; Task 2.6/2.8 precede the timer wiring in Task 2.9.
- Task 2 must complete before Task 3.1 because the API-client round trip uses the expanded response schemas while the warning tests are assembled; the round-trip test remains red until Task 3.2 adds `JobResponse.warnings`.
- Tasks 1–3 and Task 4.1–4.2 must be green before the two fresh reviews; reviews must be clean before Task 4.4 can create the implementation commit.
- The metadata commit depends on the immutable implementation hash and clean reviews; post-metadata gates depend on both commits.

## Risks and explicit validation

- **Wall-clock changes:** `time.time()` can move backward. API, health, warning helper, and dashboard helper elapsed values clamp to zero; do not substitute monotonic time because it cannot survive Redis/process restart.
- **Store transition races:** Redis status/timestamp updates must use exactly one Lua `eval` call that reads and writes atomically. Concurrent tests must assert the final status/timestamp invariant rather than mocked command order.
- **Missing timestamp corruption:** BOOTING hashes without a timestamp are invalid and must raise chained `CacheCorruptedError`; healthy/non-BOOTING hashes with an absent field normalize to `None`. The warning helper's defensive skip must not become a store compatibility path.
- **Monitor restart warning duplication:** the timestamp is persisted but the one-warning marker is monitor-local and keyed by `(worker_id, booting_since)`; verify same-ID re-registration/new timestamp creates a fresh lifecycle.
- **Stale snapshots:** HealthMonitor receives a worker snapshot before status updates. Re-read the store after BOOTING entry, or otherwise use the persisted result, before calculating elapsed so the first response is not based on a missing timestamp.
- **Accepted-job semantics:** warning inspection happens after `submit_job()` and is best effort. A BOOTING fleet must never produce a submission gate or alter HTTP 201/CLI exit 0.
- **Dashboard helper drift:** exercise the browser-independent helper behavior directly; keep source-text checks for the 1000ms interval and DOM wiring only. The browser timer must use the same whole-second/timeout-clamp contract.
- **Template compatibility:** dashboard tests currently send minimal worker dictionaries. Guard new Jinja accesses in the BOOTING branch/defaults so healthy/offline/error fixtures remain valid.
- **Metadata drift:** regenerate line references after formatting/review fixes. Update only the three target story blocks and document summaries; never point `fixed_in` at the metadata-only commit.
- **Scope regression:** explicitly inspect that OPS-007's `/partials/status` endpoint and HTML contract are untouched, and rerun full first-run plus TLS/auth/non-root/Compose coverage through `just validate` and `just first-run --step 3`.
