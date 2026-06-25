---
bundle: B22
name: Tests — app + integration
severity: MIXED
stories: 7
m_effort: 0
main_plan: 2026-06-24-code-review-tackle-round-2.md
---

# B22 — Tests — app + integration (TEST-008, -012, -013, -016, -017, DATA-005, -009)

> **For agentic workers:** Use the **Common Workflow** from the main plan. Each task is S-effort with coarse detail. Read the full story text in `docs/code_review/verification.md` before implementing.

**Bundle summary:** Test quality and coverage fixes for the app surface, the integration tests, and the data store round-trips. All small surgical changes.

**Expected commits:** 5-6.

---

## Tasks

### Task 1: TEST-008 — `worker_sdk/app._build_price_source` static/runpod-missing-key branches untested

**Files:** `tests/worker_sdk/test_app.py` (add tests only).

**Change:** add 2 unit tests: (1) `_build_price_source("static", settings)` returns the static source; (2) `_build_price_source("runpod", settings_without_api_key)` raises a clear `WorkerError` (or returns a stub if the function handles the missing-key case).

**Commit:** `test(TEST-008): add direct unit tests for _build_price_source branches`.

---

### Task 2: TEST-012 — `test_step_handler.py` mutates module-level `default_worker_factory` in a test

**Files:** `tests/shell/test_step_handler.py`.

**Change:** find the test that mutates the module-level; move the override to a `monkeypatch.setattr(..., autospec=True)` fixture. The test should clean up automatically.

**Test:** the test should still pass; add 1 assertion that the module-level is restored after the test.

**Commit:** `test(TEST-012): move module-level default_worker_factory mutation to monkeypatch fixture`.

---

### Task 3: TEST-013 — `test_edge_http.py` and `test_edge_http_multipart.py` don't assert `X-Acheron-Metadata` propagation

**Files:** `tests/worker_sdk/test_edge_http.py`; `tests/worker_sdk/test_edge_http_multipart.py`.

**Change:** add 2 tests asserting that a metadata header sent to `/execute` round-trips to the `BytesInput.metadata` and back.

**Commit:** `test(TEST-013): add X-Acheron-Metadata round-trip tests in edge_http tests`.

---

### Task 4: TEST-016 — `workers/translategemma/tests/test_handler.py:235-241` class-level mutation anti-pattern

**Files:** `workers/translategemma/tests/test_handler.py`.

**Change:** refactor the class-level mutation to a fixture-based setup. The test should not affect other tests in the file.

**Test:** the test should still pass; add 1 assertion that the class attribute is restored after the test.

**Commit:** `test(TEST-016): refactor class-level mutation in translategemma test_handler.py to fixture`.

---

### Task 5: TEST-017 — `tests/integration/test_tls.py` hardcodes 3 repo-relative paths via `Path(__file__).resolve().parents[2]`

**Files:** `tests/integration/test_tls.py`; `tests/integration/conftest.py` (add fixture).

**Change:** add a `repo_root: Path` fixture in `tests/integration/conftest.py` that returns the resolved repo root; replace the 3 hardcoded `parents[2]` paths with the fixture.

**Test:** the existing integration tests should still pass.

**Commit:** `test(TEST-017): replace hardcoded repo-relative paths in test_tls.py with a fixture`.

---

### Task 6: DATA-005 — `RedisWorkerStore._deserialize_worker` invalid status field has no corruption test

**Files:** `tests/shell/stores/test_redis_worker_store.py` (add tests only).

**Change:** add 2 tests: (1) deserialize a worker blob with `status: "INVALID"`; assert the deserializer raises a clear error. (2) deserialize a worker blob with missing `status` field; assert a clear error.

**Commit:** `test(DATA-005): add corruption tests for RedisWorkerStore._deserialize_worker`.

---

### Task 7: DATA-009 — `TestValidateChunkingFitsWorkers` has no boundary-condition test

**Files:** `tests/core/test_planner.py`.

**Change:** add 4 tests: (1) `estimated_tokens == max_input_tokens` (boundary) passes; (2) `estimated_tokens == max_input_tokens + 1` raises; (3) `max_input_tokens=0` is ignored (the check skips it); (4) empty `capabilities` tuple passes.

**Commit:** `test(DATA-009): add boundary-condition tests for validate_chunking_fits_workers`.

---

## Bundle summary

- **Stories:** 7 (all S).
- **Commits:** 5-6.
- **Cross-bundle:** none.
- **Surface to user if:** the `repo_root` fixture clashes with an existing fixture in the same `conftest.py` (or a parent one).
