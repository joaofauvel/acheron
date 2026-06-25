---
bundle: B5
name: Settings sprawl B — model_id, output_mode
severity: MEDIUM
stories: 4
m_effort: 0
main_plan: 2026-06-24-code-review-tackle-round-2.md
---

# B5 — Settings sprawl B (CFG-007, -008, -010, -011)

> **For agentic workers:** Use the **Common Workflow** from the main plan. Each task is S-effort with coarse detail. Read the full story text in `docs/code_review/architecture.md` before implementing.

**Bundle summary:** Make `WorkerSettings.model_id` and `output_mode` actually control something (CFG-007/008/010), and source `max_input_tokens` from settings (CFG-011). Touches `src/acheron/worker_sdk/settings.py` and the 3 worker packages.

**Expected commits:** 3 (one per worker YAML + handler wiring).

---

## Tasks

### Task 1: CFG-007 — `WorkerSettings.model_id` and `output_mode` are config knobs that don't control anything

**Files:** `src/acheron/worker_sdk/settings.py`; `workers/qwen3tts/handler.py`; `workers/granite_speech/handler.py`; test.

**Change:** wire `model_id` from `self._settings.model_id` (replace hardcoded string) in qwen3tts and granite_speech. The translategemma worker already does this (per the story). For `output_mode`, do the same.

**Test:** instantiate the worker with `WorkerSettings(model_id="custom/model")`; assert the handler reads the new value (e.g. via a `getattr(self._model, "name", None)` or by spying on the loader call).

**Commit:** `fix(CFG-007): wire WorkerSettings.model_id and output_mode in qwen3tts + granite_speech`.

---

### Task 2: CFG-008 — `WorkerSettings.model_id` set in 4 YAML files but still not read in code

**Files:** same as Task 1.

**Change:** this is the same fix as Task 1 (CFG-007 and CFG-008 are two angles on the same root cause: the field exists but is not consumed). Land them together.

**Test:** as in Task 1.

**Commit:** covered by CFG-007's commit (no separate commit needed).

---

### Task 3: CFG-010 — `WorkerSettings.model_id` consumed only by `translategemma`; qwen3tts and granite_speech hard-code

**Files:** same as Task 1.

**Change:** this is the same fix as Tasks 1 and 2 — wire the field in qwen3tts and granite_speech. Land all 3 as one commit.

**Test:** see Task 1.

**Commit:** covered by CFG-007's commit.

---

### Task 4: CFG-011 — `WorkerCapabilities.max_input_tokens` hard-coded `2048` in 2 workers

**Files:** `workers/qwen3tts/handler.py`; `workers/granite_speech/handler.py`; test.

**Change:** source `max_input_tokens` from `WorkerSettings` (or a per-worker default in the worker package's `pyproject.toml`/settings). The orchestrator's `validate_chunking_fits_workers` already reads from the published capability.

**Test:** instantiate the worker with `WorkerSettings(max_input_tokens=1024)`; assert the published capability has `max_input_tokens=1024`.

**Commit:** `fix(CFG-011): source WorkerCapabilities.max_input_tokens from WorkerSettings`.

---

## Bundle summary

- **Stories:** 4 (all S).
- **Commits:** 3 (CFG-007/008/010 collapse to 1 commit; CFG-011 is a separate commit; the test-only updates may need a 3rd commit if the YAML files change).
- **Cross-bundle:** none. This bundle is self-contained.
- **Surface to user if:** `WorkerSettings` needs a new field (vs. an existing field) for `max_input_tokens`, or the per-worker package has its own settings class.
