---
bundle: B6
name: Settings cleanup C — chars_per_token + factory
severity: MEDIUM
stories: 2
m_effort: 0
main_plan: 2026-06-24-code-review-tackle-round-2.md
---

# B6 — Settings cleanup C (CFG-009, ARCH-015)

> **For agentic workers:** Use the **Common Workflow** from the main plan. Each task is S-effort with coarse detail. Read the full story text in `docs/code_review/architecture.md` before implementing.

**Bundle summary:** Drop the duplicate `chars_per_token` default and stop threading `step_cache` through the worker factory. Both are 1-file fixes.

**Expected commits:** 2.

---

## Tasks

### Task 1: CFG-009 — `Settings.chars_per_token` is a top-level knob consumed by exactly one function with the default duplicated at the function signature

**Files:** `src/acheron/core/planner.py`; `src/acheron/shell/orchestrator.py`; test.

**Change:** drop the function-level default `chars_per_token: int = 1` (the new default from Round 1's CORR-026). Make it required. Update the orchestrator callsite to pass `self._settings.chars_per_token` (which it already does).

**Test:** call `validate_chunking_fits_workers(caps, max_chunk_length=100)` without `chars_per_token`; assert `TypeError` (missing required arg). Call with `chars_per_token=1`; assert success.

**Commit:** `refactor(CFG-009): drop duplicate chars_per_token default; require it from caller`.

---

### Task 2: ARCH-015 — `step_cache` threaded through `default_worker_factory` even though only the HTTP worker uses it

**Files:** `src/acheron/shell/worker_factory.py` (or wherever `default_worker_factory` is); `src/acheron/shell/orchestrator.py`; test.

**Change:** remove `step_cache` from the factory signature. The HTTP worker can take it from `Orchestrator._step_cache` (via a closure or a per-worker context) or from the constructor.

**Test:** existing tests on `default_worker_factory` should still pass. Add 1 test asserting the HTTP worker receives `step_cache` (wherever it now comes from).

**Commit:** `refactor(ARCH-015): stop threading step_cache through default_worker_factory`.

---

## Bundle summary

- **Stories:** 2 (all S).
- **Commits:** 2.
- **Cross-bundle:** none. B13 (ARCH-019) folds `validate_chunking_fits_workers` into `compile_plan`; that fix and this one are independent.
- **Surface to user if:** the HTTP worker genuinely needs `step_cache` from a different place and the closure/constructor change is non-trivial.
