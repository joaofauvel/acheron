---
bundle: B3
name: HttpWorker multipart edge cases (input validation + dedup)
severity: LOW
stories: 6
m_effort: 0
main_plan: 2026-06-24-code-review-tackle-round-2.md
---

# B3 — HttpWorker multipart edge cases (CORR-021, -022, -023, -024, -025, ARCH-022)

> **For agentic workers:** Use the **Common Workflow** from the main plan. Each task is S-effort with coarse detail. Read the full story text in `docs/code_review/correctness.md` and `docs/code_review/architecture.md` before implementing.

**Bundle summary:** Tighten input validation in `make_runpod_handler` and `_parse_multipart_request` (edge), and de-duplicate `_post_multipart` vs `_request`. All stories are small, isolated fixes.

**Expected commits:** 4-5 (one per logical group).

---

## Tasks

### Task 1: CORR-021 — `make_runpod_handler` doesn't validate `input_audio` is bytes

**Files:** `src/acheron/worker_sdk/cloud.py` (or wherever `make_runpod_handler` lives); test in same package.

**Change:** add `isinstance(payload.get("input_audio"), (bytes, bytearray))` check; raise `WorkerError` with the actual type if not.

**Test:** call the handler with `payload={"input_audio": "not-bytes"}`; assert `WorkerError`.

**Commit:** `fix(CORR-021): validate input_audio is bytes in make_runpod_handler`.

---

### Task 2: CORR-022 — `make_runpod_handler` doesn't validate `content_type` is a string

**Files:** same as Task 1.

**Change:** `isinstance(payload.get("content_type"), str)` check; raise `WorkerError` with the actual type if not.

**Test:** payload with `content_type=None`; assert `WorkerError`.

**Commit:** `fix(CORR-022): validate content_type is a string in make_runpod_handler`.

---

### Task 3: CORR-023 — `_run_execute_multipart` only catches `WorkerError` from the parser

**Files:** `src/acheron/worker_sdk/_edge_http.py`; test in `tests/worker_sdk/test_edge_http.py`.

**Change:** catch `(WorkerError, ValueError, KeyError)` around the parser call; re-raise as `WorkerError` with a sanitised message.

**Test:** mock the parser to raise `JSONDecodeError`; assert `WorkerError` is raised with a clean message (no raw traceback).

**Commit:** `fix(CORR-023): widen exception catch in _run_execute_multipart and re-raise as WorkerError`.

---

### Task 4: CORR-024 — edge `_parse_multipart_request` hardcodes `BytesInput.metadata={}`

**Files:** `src/acheron/worker_sdk/_edge_http.py`; test.

**Change:** extract per-part metadata (Task 1 of B2 does the same for the response side; do the equivalent on the request side here). Propagate into `BytesInput.metadata`.

**Test:** multipart request with a part having `X-Acheron-Metadata`; assert the resulting `BytesInput.metadata` is populated.

**Commit:** `fix(CORR-024): propagate per-part metadata in _parse_multipart_request`.

---

### Task 5: CORR-025 — edge `_parse_multipart_request` treats any non-JSON part as audio

**Files:** `src/acheron/worker_sdk/_edge_http.py`; test.

**Change:** check the part's `Content-Type` starts with `audio/`; raise `WorkerError` if a non-JSON part is not audio.

**Test:** multipart request with a non-JSON, non-audio part (e.g. `Content-Type: text/plain`); assert `WorkerError` with a clear message.

**Commit:** `fix(CORR-025): require non-JSON parts to have audio/ content type in _parse_multipart_request`.

---

### Task 6: ARCH-022 — `HttpWorker._post_multipart` is a near-byte-duplicate of `HttpWorker._request`

**Files:** `src/acheron/shell/transports/http.py`; test.

**Change:** make `_post_multipart` a one-line wrapper around `_request` (parametrise the method override if needed; or extract a shared `_send_with_multipart_body` helper).

**Test:** no behaviour change; the existing tests on both methods should still pass. Add 1 test asserting that `_post_multipart` calls `_request` (or use a `git grep` to confirm no duplication remains).

**Commit:** `refactor(ARCH-022): collapse _post_multipart into _request via shared helper`.

---

## Bundle summary

- **Stories:** 6 (all S).
- **Commits:** 4-5.
- **Cross-bundle:** Tasks 4 and 5 (CORR-024, -025) are the request-side mirrors of B2's CORR-013, -028. Land them together if the touched code is in the same file.
- **Surface to user if:** the `make_runpod_handler` constructor signature changes.
