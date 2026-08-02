# Redis Schema Bundle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `03-redis-schema` for `MAINT-018`: version persisted Redis job records, read unversioned legacy records safely, and keep valid jobs visible when one record is corrupt.

**Architecture:** `src/acheron/shell/stores/redis.py` remains the single persistence boundary. New records carry a top-level integer schema version. Read-time normalization treats missing versions as legacy version 0, applies deterministic defaults, and leaves Redis unchanged. `get()` remains strict; `list_all()` isolates malformed records so one bad job cannot hide valid jobs.

**Tech Stack:** Python 3.14, redis/aioredis, Pydantic/domain models, pytest, Redis testcontainers, `uv`.

## Global Constraints

- Keep public `JobStore`, `TrackedJob`, and API response interfaces unchanged.
- Use a stable UTC epoch/default for missing legacy timestamps; never use “now” on every read.
- Reject malformed and future schema versions with chained `CacheCorruptedError`.
- Do not silently write back migrated records during reads.
- Preserve deterministic ordering and do not log raw Redis blobs or sensitive job payloads.
- Use TDD, no `Any`, and run `just validate`, `just ux-validate`, and the independent recovery journey before verification.

## File Map

- Modify: `src/acheron/shell/stores/redis.py` — schema version, normalization, strict get, resilient list.
- Test: `tests/shell/stores/test_redis_job_store.py` — legacy fixtures, migration, future versions, and partial corruption.
- Test: `tests/shell/test_job_store.py` — parity checks for shared filtering/archive behavior.
- Metadata last: `docs/ux_review/maint.md`, `docs/ux_review/summary.md`.

## Compatibility Contract

```python
_JOB_SCHEMA_VERSION = 1

# Newly serialized records:
{
    "schema_version": 1,
    "job_id": "...",
    # existing fields unchanged
}

# Read behavior:
# missing schema_version -> version 0 normalization
# schema_version == 1 -> current validation
# schema_version > 1 or non-integer -> CacheCorruptedError
```

Version-0 normalization defaults `label=None`, `retries_from=None`, `archived_at=None`, an empty `JobProgressState`, absent `plan=None`, absent `result=None`, and stable legacy timestamps. Existing nested plan/result fields remain validated by the current constructors.

## Tasks

### Task 1: Add failing fixtures for current, legacy, malformed, and future records

**Files:**
- Test: `tests/shell/stores/test_redis_job_store.py`

- [ ] Add a helper that creates the current fully populated `TrackedJob` used by existing round-trip tests.
- [ ] Add an unversioned minimal legacy JSON record missing `schema_version`, `label`, `retries_from`, timestamps, `progress`, optional `plan`, and optional `result`.
- [ ] Add an unversioned populated legacy record retaining a plan/result but missing newer lifecycle fields.
- [ ] Add malformed JSON and future-version JSON fixtures.
- [ ] Add `test_serialize_job_writes_current_schema_version`.
- [ ] Add `test_get_unversioned_legacy_record_defaults_visibility_fields`.
- [ ] Add `test_get_unversioned_legacy_record_preserves_plan_and_result`.
- [ ] Add `test_get_malformed_schema_version_raises_cache_corrupted` and `test_get_future_schema_version_raises_cache_corrupted`.
- [ ] Run `uv run pytest --no-cov tests/shell/stores/test_redis_job_store.py -q`; confirm the new tests fail against direct indexing.

### Task 2: Add the schema-version and normalization seam

**Files:**
- Modify: `src/acheron/shell/stores/redis.py`
- Test: `tests/shell/stores/test_redis_job_store.py`

**Interfaces:**
- `_JOB_SCHEMA_VERSION: Final[int] = 1`.
- `_normalize_job_payload(data: object) -> dict[str, object]`.
- `_migrate_v0_payload(data: Mapping[str, object]) -> Mapping[str, object]`.

- [ ] Serialize `schema_version` at the top level in `_serialize_job`.
- [ ] Validate that decoded JSON is an object and that `schema_version` is an integer, defaulting an absent field to version 0.
- [ ] Normalize version 0 using deterministic timestamp/progress defaults without mutating the Redis payload.
- [ ] Reject unsupported future versions with a `CacheCorruptedError` chained from the validation exception.
- [ ] Keep nested enum, timestamp, finite-number, plan, result, and request validation unchanged.
- [ ] Run all legacy/current/future-version tests and confirm they pass.
- [ ] Commit with `git commit -m "fix(MAINT-018): version and migrate Redis job records"`.

### Task 3: Preserve strict direct reads and resilient list visibility

**Files:**
- Modify: `src/acheron/shell/stores/redis.py`
- Test: `tests/shell/stores/test_redis_job_store.py`

- [ ] Keep `RedisJobStore.get()` strict: malformed records raise `CacheCorruptedError` with the job ID in the safe message.
- [ ] Change `RedisJobStore.list_all()` to fetch IDs deterministically, deserialize one record at a time, and continue after a corrupt record.
- [ ] Log a warning with only the job ID and typed corruption summary; do not include raw JSON/blob contents.
- [ ] Keep the corrupt ID in the Redis set so operators can repair or delete it explicitly.
- [ ] Preserve existing `delete()` semantics and document its atomic removal boundary in the tests; do not broaden this bundle into a delete API redesign.
- [ ] Add `test_list_all_skips_corrupt_record_without_hiding_valid_jobs` with current, legacy, and malformed records.
- [ ] Add `test_list_all_warns_without_logging_raw_blob` using `caplog`.
- [ ] Add `test_get_corrupt_record_remains_strict`.
- [ ] Run `uv run pytest --no-cov tests/shell/stores/test_redis_job_store.py -q`.
- [ ] Commit with `git commit -m "fix(MAINT-018): preserve visible jobs around corrupt records"`.

### Task 4: Verify store parity and operator recovery

**Files:**
- Test: `tests/shell/stores/test_redis_job_store.py`
- Test: `tests/shell/test_job_store.py`
- Modify: `docs/ux_review/maint.md`
- Modify: `docs/ux_review/summary.md`

- [ ] Run the Redis and in-memory store suites together and confirm archive/delete/filter behavior remains unchanged.
- [ ] Seed a representative pre-change Redis record, restart the orchestrator, run the jobs listing path, and confirm the record remains visible.
- [ ] Confirm one malformed record does not hide valid current or legacy records and the warning identifies the affected job.
- [ ] Run `just validate` and `just ux-validate`.
- [ ] Independently perform the MAINT-018 journey: persist an old record, deploy the new code, list/recover it, and record the evidence path.
- [ ] Refresh MAINT-018 citations and set `fixed_in`, `verified_in`, `last_verified_at`, and `verified_by` only after evidence; retain `bundle: 03-redis-schema`.
- [ ] Run `just ux-verify MAINT-018` and `git diff --check`.
- [ ] Commit with `git commit -m "docs(ux-review): close Redis schema bundle evidence"`.

## Completion Gate

- [ ] New records contain `schema_version=1`.
- [ ] Unversioned legacy records are visible with deterministic defaults.
- [ ] Future/malformed versions raise typed errors.
- [ ] `list_all()` returns valid jobs around corrupt records and warns safely.
- [ ] `get()` remains strict.
- [ ] `just validate`, `just ux-validate`, and `just ux-verify MAINT-018` pass.
