---
bundle: B19
name: MAINT cleanup & Python 2 syntax
severity: MIXED
stories: 8
m_effort: 1
main_plan: 2026-06-24-code-review-tackle-round-2.md
---

# B19 — MAINT cleanup & Python 2 syntax (MAINT-002, -005, -006, -007, -008, -009, -012, CORR-031)

> **For agentic workers:** Use the **Common Workflow** from the main plan. **Tackle in this order: MAINT-009 + CORR-031 (Python 2 syntax) → MAINT-005 → MAINT-008 → MAINT-012 → MAINT-006 → MAINT-007 → MAINT-002.** The Python 2 syntax fix is mechanical and unblocks 8 sites. Save MAINT-002 (M-effort redis JSON) for last.

**Bundle summary:** Fix Python 2 `except A, B:` syntax (8 sites across 6 files), extract a few small helpers, drop 1 redundant parameter re-assignment, and (M-effort) extract a shared JSON ser/deser helper for the redis store and the in-memory cache.

**Expected commits:** 5-6.

---

## Tasks (tackle in order)

### Task 1: MAINT-009 + CORR-031 — Python 2-style `except A, B:` syntax used at 7 sites across 6 files (CORR-031 adds an 8th site in `HttpWorker.health`)

**Story:** `docs/code_review/code-quality.md` § MAINT-009 (LOW, S); `correctness.md` § CORR-031 (LOW, S).

**Files:** find the 8 sites with `grep -n 'except [A-Z][a-zA-Z]*, [a-z]' src/`; likely candidates are `shell/health.py`, `shell/orchestrator.py`, `shell/api/routes.py`, `worker_sdk/app.py`, `worker_sdk/_edge_http.py`, `transports/http.py`, plus `HttpWorker.health`.

**Change:** replace `except A, B:` with `except A as B:`. Mechanical; the regex is `except ([A-Z][a-zA-Z0-9_.]*), ([a-z][a-zA-Z0-9_]*):` → `except \1 as \2:`.

**Test:** mypy clean. Existing tests pass.

**Commit:** `fix(MAINT-009, CORR-031): replace except A, B: syntax with except A as B: across 8 sites`.

---

### Task 2: MAINT-005 — `Orchestrator._execute` duplicates `PlanResult` construction across adjacent branches

**Files:** `src/acheron/shell/orchestrator.py`; test.

**Change:** extract `_build_plan_result(stage_outputs: dict[str, list[Output]]) -> PlanResult`; call it from each branch.

**Test:** existing tests should still pass; add 1 unit test on the new helper.

**Commit:** `refactor(MAINT-005): extract _build_plan_result in Orchestrator to remove branch duplication`.

---

### Task 3: MAINT-008 — `HealthMonitor._handle_failure` reassigns its `error` parameter inside the function

**Files:** `src/acheron/shell/health.py`; test.

**Change:** rename the local reassignment to `_error` and use a new local for the (optional) wrapped error.

**Test:** existing tests should still pass.

**Commit:** `refactor(MAINT-008): rename _handle_failure parameter reassignment to avoid shadowing`.

---

### Task 4: MAINT-012 — `_registration_caps` manually re-lists every `WorkerCapabilities` field

**Files:** `src/acheron/worker_sdk/registration.py`; test.

**Change:** replace the manual dict construction with `WorkerCapabilities.model_dump(mode="json")`.

**Test:** existing tests should still pass; add 1 test asserting the dumped dict has all expected keys.

**Commit:** `refactor(MAINT-012): use WorkerCapabilities.model_dump in _registration_caps`.

---

### Task 5: MAINT-006 — `Orchestrator.start()` inlines 17-line registration-token block; logs the token

**Files:** `src/acheron/shell/orchestrator.py`; test.

**Change:** extract `_resolve_registration_token(settings: Settings, token_file: Path | None) -> str` as a free function (it's currently a method on the orchestrator; the orchestrator just delegates). Note: Round 1's SEC-011 already added validation; this is the structural extraction.

**Test:** existing tests should still pass; add 1 unit test on the new free function.

**Commit:** `refactor(MAINT-006): extract _resolve_registration_token as a free function`.

---

### Task 6: MAINT-007 — `RunPodHealthProvider` and `HuggingFaceHealthProvider` duplicate the HTTP fetch envelope

**Files:** `src/acheron/shell/health_providers.py`; test.

**Change:** extract `_async_fetch(url: str, *, headers: dict | None = None, timeout: float) -> tuple[int, bytes]` to a shared base class or helper.

**Test:** existing tests should still pass; add 1 unit test on the new helper.

**Commit:** `refactor(MAINT-007): extract _async_fetch to share HTTP envelope in health providers`.

---

### Task 7: MAINT-002 (M) — `redis.py` hand-rolls JSON ser/deser for domain models that `cache.py` serializes via Pydantic

**Story:** `docs/code_review/code-quality.md` § MAINT-002 (MEDIUM, M effort).

**Files:**
- Modify: `src/acheron/shell/serialization.py` (new).
- Modify: `src/acheron/shell/stores/redis.py`; `src/acheron/shell/cache.py`.
- Test: `tests/shell/test_serialization.py` (new).

**Design:**

```python
# shell/serialization.py
from pydantic import BaseModel


def serialize(model: BaseModel) -> bytes:
    return model.model_dump_json().encode("utf-8")


def deserialize(model: type[T], blob: bytes) -> T:
    return model.model_validate_json(blob.decode("utf-8"))
```

Both `redis.py` and `cache.py` use it.

**Test:** 3 unit tests on the new module (round-trip a simple model, round-trip a complex nested model, error on invalid input).

**Commit:** `refactor(MAINT-002): extract serialize/deserialize helpers to shell/serialization.py; use in redis and cache`.

---

## Bundle summary

- **Stories:** 8 (1 M-effort: MAINT-002; 7 S-effort).
- **Commits:** 5-6 (the Python 2 syntax fix is 1 commit; the others are 1 per story).
- **Order matters:** Python 2 syntax first (mechanical); then small refactors; MAINT-002 last (M-effort, biggest change).
- **Cross-bundle:** none.
- **Surface to user if:** the Python 2 `except` syntax is in a file that's not in `src/` (e.g. tests, stubs), or the `_async_fetch` extraction conflicts with B7's OBS-005 fix (also touches health_providers).
