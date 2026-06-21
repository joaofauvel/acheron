# Layer 10 Adversarial Review

**Date:** 2026-06-21
**Reviewer:** subagent (adversarial)
**Scope:** Built-in Local Workers & Resuming Core (Layer 10 plan & spec)
**Verdict:** needs changes

## Summary

The design for Layer 10 (Local Workers, Pydantic settings configuration, resuming core, and click CLI restructure) is architecturally solid. However, the implementation plan has several critical and major gaps that would cause runtime failures, test flakiness, and memory leaks. The most severe issues are:
1. A plan detail that would revert the environment variable override priority for settings configuration.
2. A cache-miss failure when running steps in `SEQUENTIAL` or `ASYNC` execution strategies because they do not write to the step cache.
3. An ineffective idempotency guard in the orchestrator `_execute` method that checks in-memory instead of database state.
4. A permanent lockout on job resumes if a job is in a stale `RUNNING` state due to a crash.

---

## Findings

### Critical

**RED-10-1: Reversion of Pydantic Settings Customise Sources**
- **Location:** `docs/superpowers/plans/2026-06-21-layer10-local-workers-and-resuming.md` (Task 1, Step 4)
- **Issue:** The plan proposes to overwrite `config.py` using `Settings(**data)` directly in the constructor. In Pydantic Settings v2, passing settings as kwargs in the constructor populates the `init_settings` source, which has the highest precedence. Consequently, environment variables (e.g. `ACHERON_ORCHESTRATOR__DATA_DIR`) will no longer override configuration loaded from the YAML file. This breaks spec requirement **F-05**.
- **Suggestion:** Retain the existing `settings_customise_sources` method in `src/acheron/shell/config.py` which registers a lower-priority custom `YamlConfigSettingsSource` to guarantee env-var overrides take precedence.

**RED-10-2: Breakage of Sequential/Async Executors with Local Workers**
- **Location:** `src/acheron/shell/executors/` and `src/acheron/shell/local_handlers.py`
- **Issue:** Local worker handlers (`ChunkingHandler` and `PackagingHandler`) load their inputs from the `StepCache` (F-03). However, `SequentialExecutor` and `AsyncExecutor` do not write step outputs to the `StepCache` during execution. If a job is executed with either strategy, subsequent steps will fail with a `CacheMissError` (wrapped in `WorkerError`).
- **Suggestion:** Wrap the step handler inside `Orchestrator._execute` for non-streaming executors so that successful step outputs are automatically written to `StepCache` globally across all strategies, or modify executors to accept and write to `StepCache`.

### Major

**RED-10-3: No-op Idempotency Guard in Orchestrator `_execute`**
- **Location:** `src/acheron/shell/orchestrator.py` (Task 8, Step 4)
- **Issue:** The proposed idempotency guard checks `tracked.status != PlanStatus.RUNNING` on the local in-memory `tracked` object. Since the orchestrator sets `tracked.status = PlanStatus.RUNNING` in memory right before spawning the task, this check will always pass, ignoring any concurrent status changes in the persistent store.
- **Suggestion:** Query the persistent job store directly before performing the check:
  ```python
  db_job = await self._job_store.get(tracked.job_id)
  if db_job is None or db_job.status != PlanStatus.RUNNING:
      logger.warning("Idempotency guard: job %s has database status %s, skipping execution", tracked.job_id, db_job.status if db_job else "None")
      return
  ```

**RED-10-4: Stuck `RUNNING` State Lock-out on Resume**
- **Location:** `src/acheron/shell/orchestrator.py` (Task 8, Step 4)
- **Issue:** The `/resume` API route rejects jobs with status `RUNNING` with `400 Bad Request`. If the orchestrator process crashes mid-execution, the job is left permanently marked as `RUNNING` in the persistent store. The user can never resume it.
- **Suggestion:** Check if the job is active in the current orchestrator's task list (`self._active_jobs`). If it is not active, allow resumption to proceed and warn about the stale status override.

### Minor & Nits

**RED-10-5: Integration Test Environment Variable Mismatch**
- **Location:** `tests/integration/conftest.py`
- **Issue:** The plan exports `ACHERON_DATA_DIR` in the `wired_orchestrator` fixture. However, the orchestrator config uses `ACHERON_ORCHESTRATOR__DATA_DIR` to override its job data directory. The stubs will write to `tmp_path` while the orchestrator looks in `/data/jobs`, causing cache misses.
- **Suggestion:** Export both `ACHERON_DATA_DIR` and `ACHERON_ORCHESTRATOR__DATA_DIR` to `str(tmp_path)` in the fixture.

**RED-10-6: HTML Tag Stripper Merges Words on Self-Closing Block Tags**
- **Location:** `src/acheron/shell/local_handlers.py`
- **Issue:** `HTMLStripper.handle_endtag` adds spaces to block tags, but self-closing block elements like `<br>` or `<br />` do not trigger `handle_endtag` in Python's `HTMLParser`, merging words across boundaries.
- **Suggestion:** Override `handle_starttag` to also append a space when block tags are opened.

**RED-10-7: Memory Leak in Orchestrator `_job_locks`**
- **Location:** `src/acheron/shell/orchestrator.py`
- **Issue:** Using a standard dictionary for `self._job_locks` leaks memory as lock entries are added but never removed.
- **Suggestion:** Use `weakref.WeakValueDictionary` for `self._job_locks` so locks are automatically garbage-collected.

**RED-10-8: Flaky Timing-Dependent Test for Resume Route**
- **Location:** `tests/shell/api/test_jobs.py`
- **Issue:** The test relies on a fixed `await asyncio.sleep(0.5)` which is timing-dependent and flaky under load.
- **Suggestion:** Poll the job status until it transitions away from `running` before triggering the `/resume` request.

---

## Round 2 (post-implementation review)

**Date:** 2026-06-21
**Verdict:** all findings addressed

All 8 prior findings (RED-10-1 through RED-10-8) were properly addressed in the implementation.

### New findings (round 2)

**RED-10-9: Empty checksums in stubs defeat cache-skip resume** — Major — Fixed
- Stubs wrote `"checksum": ""` in manifests; `step_has_valid_cache` compares computed SHA-256 against `output.checksum`, so it always returned `False`, defeating resume. Fixed by computing real checksums in all stubs and test handlers.

**RED-10-10: CacheCorruptedError not caught when loading upstream outputs** — Minor — Fixed
- `ChunkingHandler` and `PackagingHandler` caught `CacheMissError, OSError` but not `CacheCorruptedError`. Added to both except clauses.

**RED-10-11: PackagingHandler concat paths not resolved to absolute** — Minor — Fixed
- Used `Path(out.path).as_posix()` without `.resolve()`. FFmpeg resolves relative to CWD, not inputs.txt. Fixed with `.resolve()`.

**RED-10-12: worker_stub else branch writes to "translate" for all non-TTS** — Minor — Accepted
- The else branch writes to `translate/` for any non-TTS type. Currently only used for TRANSLATION (separate stub handles ASR inline). Latent bug if reused for ASR; accepted as low risk.

**RED-10-13: Missing test coverage for resume edge cases** — Minor — Accepted
- No `force_fresh=True` API test, no 400 response test for active jobs at route level, no re-execution verification. Accepted as technical debt for future test improvement.

**RED-10-14: test_resume_job_route leaves spawned _execute task uncleaned** — Nit — Accepted
- Background task cancelled on loop teardown. May produce warnings. Accepted as low impact.

**RED-10-15: shutil.rmtree without ignore_errors** — Nit — Fixed
- Added `ignore_errors=True` to prevent TOCTOU race.

**RED-10-16: client fixture doesn't call orch.close()** — Nit — Accepted
- Inconsistent with `wired_app` fixture but harmless for InMemory stores.

**RED-10-17: YAML config silently swallows malformed errors** — Nit — Fixed
- Changed blind `except Exception` to catch `yaml.YAMLError` with a warning log and `OSError` silently. Malformed explicit config now warns.
