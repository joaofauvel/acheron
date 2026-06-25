---
bundle: B13
name: Plan-time / chunking & OBS-011
severity: MIXED
stories: 3
m_effort: 0
main_plan: 2026-06-24-code-review-tackle-round-2.md
---

# B13 — Plan-time / chunking (ARCH-019, OBS-011, DOC-006)

> **For agentic workers:** Use the **Common Workflow** from the main plan. Each task is S-effort with coarse detail. Read the full story text in `docs/code_review/architecture.md`, `operations.md`, `surface.md` before implementing.

**Bundle summary:** Fold the post-step `validate_chunking_fits_workers` into `compile_plan`, add operator-visible log lines, complete the `Raises:` docstrings.

**Expected commits:** 3.

---

## Tasks

### Task 1: ARCH-019 — `validate_chunking_fits_workers` is a post-step in `submit_job` that should be folded into `compile_plan`

**Files:** `src/acheron/core/planner.py`; `src/acheron/shell/orchestrator.py`; tests in `tests/core/test_planner.py` and `tests/shell/test_orchestrator.py`.

**Change:** move the `validate_chunking_fits_workers(...)` call from `Orchestrator.submit_job` (where it runs after `compile_plan`) into `compile_plan` (or its caller). The orchestrator no longer needs to call it explicitly.

**Test:** the existing test `test_submit_job_chunking_too_long_raises` should still pass (the check still runs, just earlier). The existing test `test_submit_job_chunking_fits` should still pass.

**Commit:** `refactor(ARCH-019): fold validate_chunking_fits_workers into compile_plan`.

---

### Task 2: OBS-011 — `validate_chunking_fits_workers` runs in `submit_job` with no log on success or failure

**Files:** `src/acheron/core/planner.py` (or its caller); test.

**Change:** add `logger.debug(...)` on the success path (with the max estimated tokens and the limiting worker's `max_input_tokens`); add `logger.warning(...)` on the error path (with the full error message — this is a guard rail, not a security boundary, so logging the full error is OK).

**Test:** use `caplog` to assert the log line is emitted with the expected fields.

**Commit:** `fix(OBS-011): add log lines for validate_chunking_fits_workers success and failure`.

---

### Task 3: DOC-006 — `submit_job` and `validate_chunking_fits_workers` have incomplete Google-style `Raises:` sections

**Files:** `src/acheron/shell/orchestrator.py`; `src/acheron/core/planner.py`.

**Change:** add `ChunkingTooLongForWorkerError` to `Orchestrator.submit_job`'s `Raises:` section; add a full `Raises:` section to `validate_chunking_fits_workers` documenting `ValueError` and `ChunkingTooLongForWorkerError`.

**Test:** no new test; the existing tests should still pass.

**Commit:** `docs(DOC-006): complete Raises: sections for submit_job and validate_chunking_fits_workers`.

---

## Bundle summary

- **Stories:** 3 (all S).
- **Commits:** 3.
- **Cross-bundle:** B6 (CFG-009) also touches `validate_chunking_fits_workers` (drops the duplicate default). Land B6 first or coordinate the touches.
- **Surface to user if:** the `compile_plan` refactor (Task 1) needs a new dependency on `Settings` (e.g. `chars_per_token`); that may pull in B6.
