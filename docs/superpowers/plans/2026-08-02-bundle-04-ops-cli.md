# Operator CLI Bundle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `04-ops-cli` for `OPS-015` and `OPS-032`: expose sanitized TTS model/voice capability data end-to-end and add actionable missing-job remediation to `acheron job tail`.

**Architecture:** The API’s typed capability projection is the public contract: it includes allowlisted `model_source` and bounded `speakers`, never raw registration metadata. The CLI consumes only that typed contract. Missing-job stream errors carry the existing structured remediation field from route to client to generic CLI rendering.

**Tech Stack:** Python 3.14, FastAPI/Pydantic, httpx, Click, Rich, pytest/respx.

## Global Constraints

- Preserve metadata allowlisting and credential/URL sanitization; never expose raw worker registration metadata.
- Keep public field naming aligned with the current worker contract: `model_source` and `speakers`.
- Preserve deterministic worker ordering, type filtering, request IDs, nonzero error exits, and specialized CLI renderers.
- Use TDD and no compatibility fallback to excluded `metadata` fields.
- Update README command/help documentation in the same implementation bundle.
- Run `just validate`, `just ux-validate`, and independent operator journeys before verification.

## File Map

- `src/acheron/core/schemas.py` — public `WorkerCapability` fields.
- `src/acheron/shell/api/routes/capabilities.py` — model/speaker projection.
- `src/acheron/api_client.py` — typed response parsing.
- `src/acheron/cli.py` — capability table and structured remediation rendering.
- `src/acheron/shell/api/routes/job_streams.py` — missing-job remediation.
- Tests: `tests/core/test_schemas.py`, `tests/shell/conftest.py`, `tests/shell/api/test_capabilities.py`, `tests/shell/api/test_jobs.py`, `tests/test_api_client.py`, `tests/shell/test_cli.py`.
- `README.md` — command contract.
- `docs/ux_review/ops.md`, `docs/ux_review/summary.md` — metadata last.

## Tasks

### Task 1: Lock the public capability wire contract with failing tests

**Files:**
- Test: `tests/core/test_schemas.py`
- Test: `tests/shell/api/test_capabilities.py`
- Modify: `tests/shell/conftest.py`

- [ ] Update the TTS fixture to advertise `metadata={"speakers": ["vivian", "ryan"]}` and a model source.
- [ ] Add `test_worker_capability_public_dump_contains_model_and_speakers_only`; assert public output contains `model_source`/`speakers` and excludes `metadata`.
- [ ] Add `test_type_tts_returns_sorted_allowlisted_inventory_with_model_and_speakers`.
- [ ] Add `test_typed_capabilities_redact_unsafe_model_source_and_speakers`.
- [ ] Run `uv run pytest --no-cov tests/core/test_schemas.py tests/shell/api/test_capabilities.py -q`; confirm failures show the current excluded model field/fixture mismatch.

### Task 2: Implement sanitized capability projection

**Files:**
- Modify: `src/acheron/core/schemas.py`
- Modify: `src/acheron/shell/api/routes/capabilities.py`
- Test: `tests/shell/api/test_capabilities.py`

- [ ] Make `WorkerCapability.model_source` public and retain bounded `speakers` as the public voice field.
- [ ] Keep internal `metadata` excluded from serialized responses.
- [ ] In `get_capabilities`, pass model data through the existing public/sanitization helper and speakers through `_public_speakers()`.
- [ ] Preserve worker-type/source/destination filtering and deterministic order.
- [ ] Run the schema/route tests and assert unsafe model IDs become `null` while unsafe speakers remain excluded.
- [ ] Commit with `git commit -m "fix(OPS-015): expose sanitized model and voice capabilities"`.

### Task 3: Verify client parsing and CLI capability rendering

**Files:**
- Modify: `src/acheron/api_client.py`
- Modify: `src/acheron/cli.py`
- Test: `tests/test_api_client.py`
- Test: `tests/shell/test_cli.py`

- [ ] Confirm `AcheronClient.get_worker_capabilities()` parses the revised `WorkerCapability` without reading server-private metadata.
- [ ] Add `test_get_worker_capabilities_preserves_public_model_and_speakers`.
- [ ] Change the `capabilities` command to read `w.model_source` and `w.speakers`, rendering `-` only when absent.
- [ ] Add `test_capabilities_typed_tts_renders_model_and_available_voices` and `test_capabilities_typed_absent_model_or_speakers_renders_dash`.
- [ ] Run `uv run pytest --no-cov tests/test_api_client.py tests/shell/test_cli.py -q`.
- [ ] Run `acheron capabilities --help` and confirm help text names model and available voices.
- [ ] Commit with `git commit -m "fix(OPS-015): render typed capability details in CLI"`.

### Task 4: Add missing-job remediation to the structured API error

**Files:**
- Modify: `src/acheron/shell/api/routes/job_streams.py`
- Test: `tests/shell/api/test_jobs.py`

- [ ] Update the missing-job branch to raise `JobNotFoundError("Job not found", remediation="acheron jobs")`.
- [ ] Preserve HTTP 404, the existing `ErrorResponse`, and request-ID behavior.
- [ ] Extend `test_job_logs_404_for_missing_job` to assert `detail.remediation == "acheron jobs"`.
- [ ] Run `uv run pytest --no-cov tests/shell/api/test_jobs.py -q`.
- [ ] Commit with `git commit -m "fix(OPS-032): add missing-job tail remediation"`.

### Task 5: Render remediation in stream failures without regressions

**Files:**
- Modify: `src/acheron/cli.py`
- Test: `tests/shell/test_cli.py`

- [ ] Keep `_print_stream_error()` delegating HTTP failures to `_print_http_error()`.
- [ ] Update `_print_http_error()` to print `Try: <remediation>` when the structured error contains a safe remediation.
- [ ] Preserve request-ID output exactly once and leave specialized resume/cancel/retry renderers unchanged.
- [ ] Add `test_job_tail_missing_job_shows_jobs_remediation` with a 404 structured response and `test_job_tail_http_error_prints_request_id_once` regression coverage.
- [ ] Run focused CLI tests and invoke `acheron job tail missing-job` against the test client fixture.
- [ ] Commit with `git commit -m "fix(OPS-032): render actionable stream-error remediation"`.

### Task 6: Update command documentation and UX metadata

**Files:**
- Modify: `README.md`
- Modify: `docs/ux_review/ops.md`
- Modify: `docs/ux_review/summary.md`

- [ ] Document that `acheron capabilities --type tts` displays model and available voices; explain `-` as unavailable.
- [ ] Document `acheron job tail <job-id>` and the missing-job `Try: acheron jobs` remediation.
- [ ] Refresh OPS-015/OPS-032 line citations after implementation and retain `bundle: 04-ops-cli`.
- [ ] Set lifecycle fields only after focused tests and independent command transcripts exist.
- [ ] Run `just validate`, `just ux-validate`, `just ux-verify OPS-015`, `just ux-verify OPS-032`, and `git diff --check`.
- [ ] Commit with `git commit -m "docs(ux-review): close operator CLI bundle evidence"`.

## Completion Gate

- [ ] `acheron capabilities --type tts` shows actual model/voice data from the typed API response.
- [ ] `acheron job tail missing-job` exits nonzero, has no traceback, and prints `Try: acheron jobs`.
- [ ] Public responses exclude internal registration metadata and unsafe values.
- [ ] `just validate`, both UX verification commands, and fresh-context correctness/docs reviews pass.
