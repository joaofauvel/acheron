---
bundle: B16
name: ARCH & type safety (low)
severity: LOW
stories: 7
m_effort: 0
main_plan: 2026-06-24-code-review-tackle-round-2.md
---

# B16 — ARCH & type safety (low) (ARCH-008, -010, -013, -016, CORR-009, -016, TYPE-009)

> **For agentic workers:** Use the **Common Workflow** from the main plan. Each task is S-effort with coarse detail. Read the full story text in `docs/code_review/architecture.md`, `correctness.md`, `code-quality.md` before implementing.

**Bundle summary:** Loose-end architectural cleanups: small boundary fixes, docstring updates, and 1 type-safety fix. All S-effort, all small.

**Expected commits:** 5-6.

---

## Tasks

### Task 1: ARCH-008 — `Orchestrator.__init__` derives default `StepCache` from `PlanCache.dir` coupling

**Files:** `src/acheron/shell/orchestrator.py`; test.

**Change:** take `step_cache` as an explicit parameter; default to `InMemoryStepCache()`. Remove the implicit `step_cache=StepCache(self._plan_cache.dir)` coupling.

**Test:** `Orchestrator(...)` without `step_cache` should construct an `InMemoryStepCache`. `Orchestrator(..., step_cache=custom_cache)` should use `custom_cache`.

**Commit:** `refactor(ARCH-008): take step_cache as explicit Orchestrator parameter, default to InMemoryStepCache`.

---

### Task 2: ARCH-010 — `HealthProviders` is a no-behavior wrapper over `dict`

**Files:** `src/acheron/shell/health_providers.py` (or wherever the wrapper is); test.

**Change:** drop the wrapper; use `dict[str, HealthProvider]` directly. Update all call sites.

**Test:** existing tests should still pass; no new test.

**Commit:** `refactor(ARCH-010): drop HealthProviders wrapper, use dict[str, HealthProvider] directly`.

---

### Task 3: ARCH-013 — `transports/grpc.py` and `transports/http.py` both duplicate the `data_dir` lookup

**Files:** `src/acheron/shell/transports/grpc.py`; `src/acheron/shell/transports/http.py`; test.

**Change:** pass `data_dir` from the orchestrator to each transport's constructor. The transports' `__init__` takes `data_dir: Path` instead of reading `ACHERON_DATA_DIR` (B4's CFG-006 already removed the direct env read; this is the structural follow-up).

**Test:** existing transport tests should still pass; update the constructor calls in the tests.

**Commit:** `refactor(ARCH-013): pass data_dir to transports via constructor instead of env lookup`.

---

### Task 4: ARCH-016 — `workers/_shared` is a module (file) co-located with a same-name test dir

**Files:** `workers/_shared.py`; `workers/_shared/`; test.

**Change:** rename `workers/_shared.py` to `workers/_shared_utils.py` (the file is a module; the same-name dir is a real package). Update all imports. Alternative: rename the test dir; pick the option that requires fewer touch-points.

**Test:** all existing tests should still pass.

**Commit:** `chore(ARCH-016): rename workers/_shared.py to disambiguate from the test dir`.

---

### Task 5: CORR-009 — step handler caches worker list and worker instances across steps and plans

**Files:** `src/acheron/shell/step_handler.py`; test.

**Change:** invalidate the worker cache on `submit_job` and `cancel_job`. Add a `_invalidate_worker_cache()` method.

**Test:** existing tests should still pass; add 1 test asserting the cache is cleared after `submit_job`.

**Commit:** `fix(CORR-009): invalidate step handler worker cache on submit_job and cancel_job`.

---

### Task 6: CORR-016 — `worker_sdk` package docstring falsely claims GPU-SDK-free at import (overlap with ARCH-011)

**Files:** `src/acheron/worker_sdk/__init__.py`; `src/acheron/worker_sdk/cloud.py`.

**Change:** update the docstring to match reality (the runpod SDK is imported transitively via `cloud.py`). Same fix as B17's TYPE-009 for the runpod handler self._model annotation.

**Test:** no new test; just a docstring update.

**Commit:** `docs(CORR-016): correct worker_sdk package docstring re GPU-SDK import`.

**Note:** this overlaps with B16's TYPE-009 and B17's TYPE-006. Land them together if the touches are in the same area.

---

### Task 7: TYPE-009 — `GraniteSpeechRunpodHandler` types `self._model` and `self._processor` as `Any`

**Files:** `workers/granite_speech/handler.py`; test.

**Change:** type-annotate using the `_ModelProto`/`_ProcessorProto` Protocols introduced in B17's TYPE-010. If B17 hasn't landed yet, leave this for after B17.

**Test:** mypy should pass without `# type: ignore` on the model/processor fields.

**Commit:** `fix(TYPE-009): type GraniteSpeechRunpodHandler self._model and self._processor`.

---

## Bundle summary

- **Stories:** 7 (all S).
- **Commits:** 5-6.
- **Cross-bundle:** CORR-016 (Task 6) overlaps with TYPE-009 (Task 7) and B17's TYPE-006. Coordinate: land B17 first if it adds the Protocols that TYPE-009 depends on.
- **Surface to user if:** the `workers/_shared` rename touches more than 5 import sites.
