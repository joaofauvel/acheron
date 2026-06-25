---
bundle: B2
name: HttpWorker multipart cleanup
severity: MEDIUM
stories: 6
m_effort: 0
main_plan: 2026-06-24-code-review-tackle-round-2.md
---

# B2 — HttpWorker multipart cleanup (CORR-013, -027, -028, -030; DATA-006, -008)

> **For agentic workers:** Use the **Common Workflow** from the main plan. Each task is an S-effort story with coarse detail (file path, test, 1-line change). Read the full story text in `docs/code_review/correctness.md` (CORR-*) and `docs/code_review/verification.md` (DATA-*) before implementing.

**Bundle summary:** Fix 5 correctness bugs and 1 test gap in `_parse_multipart` / `_execute_with_upstream_input` / response-side edge cases. All stories touch `src/acheron/shell/transports/http.py` and its tests.

**Expected commits:** 3-4 (group by file: response-parser changes, dispatch change, tests).

---

## Tasks

### Task 1: CORR-013 — per-part `X-Acheron-Metadata` header is discarded

**Files:**
- Modify: `src/acheron/shell/transports/http.py` (`_parse_multipart`)
- Test: `tests/shell/transports/test_http_multipart.py` (add test)

**Change:** extract the per-part `X-Acheron-Metadata` header into a `metadata: dict[str, str]` field on the parsed part. Wire the metadata into the `ParsedPart` dataclass.

**Test:** construct a multipart body with one part having `X-Acheron-Metadata: {"key": "value"}`; assert the parsed part's `metadata` is the expected dict.

**Commit:** `fix(CORR-013): propagate per-part X-Acheron-Metadata through _parse_multipart`.

---

### Task 2: CORR-027 — `_execute_with_upstream_input` only POSTs the first matching file

**Files:**
- Modify: `src/acheron/shell/transports/http.py` (`_execute_with_upstream_input`)
- Test: `tests/shell/transports/test_http_multipart.py`

**Change:** the current code uses `next(...)` to pick the first file matching the predicate. Replace with a check that raises `WorkerError` if more than one file matches; keep the first-match behaviour for the single-file case. (B12's ARCH-020 is the structural fix; this is the immediate correctness fix.)

**Test:** register an upstream that emits 2 files matching the predicate; assert `WorkerError` is raised with a message naming the duplicate.

**Commit:** `fix(CORR-027): raise WorkerError when _execute_with_upstream_input gets multiple matching files`.

---

### Task 3: CORR-028 — `_parse_multipart` boundary extraction raises IndexError

**Files:**
- Modify: `src/acheron/shell/transports/http.py` (`_parse_multipart`)
- Test: `tests/shell/transports/test_http_multipart.py`

**Change:** guard the boundary slice; if the response's Content-Type has no `boundary=`, raise `WorkerError(f"missing boundary in Content-Type: {content_type}")` instead of letting `IndexError` propagate.

**Test:** construct a `multipart/form-data` response with `Content-Type: multipart/form-data` (no `boundary=`); assert `WorkerError` is raised with the right message.

**Commit:** `fix(CORR-028): raise WorkerError on missing boundary= in _parse_multipart`.

---

### Task 4: CORR-030 — first `application/json` is metrics; sidecar JSON overwrites

**Files:**
- Modify: `src/acheron/shell/transports/http.py` (`_parse_multipart`)
- Test: `tests/shell/transports/test_http_multipart.py`

**Change:** instead of "first JSON part wins", use an explicit `X-Acheron-Part-Name: metrics` header. If no part has the header, fall back to first JSON; if multiple parts have it, raise `WorkerError`.

**Test:** construct a multipart body with a sidecar JSON (no `X-Acheron-Part-Name` header) and a metrics JSON (header set); assert the metrics part is selected, not the sidecar.

**Commit:** `fix(CORR-030): pin metrics part by X-Acheron-Part-Name header in _parse_multipart`.

---

### Task 5: DATA-006 — `_parse_multipart` request-side edge cases (no metrics, missing boundary, non-utf8)

**Files:**
- Test: `tests/shell/transports/test_http_multipart.py` (new tests only)

**Change:** add 3 direct unit tests covering the new error paths from CORR-013, -028, -030. No code change; this is the test-coverage task that pins the fixes from tasks 1-4.

**Commit:** `test(DATA-006): add direct unit tests for _parse_multipart edge cases`.

---

### Task 6: DATA-008 — `_parse_multipart` response-side edge cases (no metrics, missing boundary, malformed body)

**Files:**
- Test: `tests/shell/transports/test_http_multipart.py` (new tests only)

**Change:** add 3 direct unit tests for the response side: (1) response body has no metrics part; (2) response Content-Type has no `boundary=`; (3) response body is malformed. The first two are unit tests on the parser; the third is a defensive `WorkerError` raise (add a `try/except MultipartError` around the parser call if not already present).

**Commit:** `test(DATA-008): add response-side edge-case tests for _parse_multipart`.

---

## Bundle summary

- **Stories tackled:** 6 (CORR-013, -027, -028, -030, DATA-006, DATA-008).
- **Commits expected:** 3-4 (the 2 DATA test tasks can be combined into 1 commit if their tests are in the same file).
- **M-effort iteration risk:** none (all S).
- **Cross-bundle dependency:** none. B3 will land similar fixes for the input-validation side; B11 will tackle memory materialization; B12 will refactor the triple-magic-string signature. B2 fixes only what the story text describes.
- **Surface to user if:** the response parser is shared with B3 (overlap), or if the existing `ParsedPart` dataclass doesn't have a `metadata` field (need to add one — this is part of the task, not a blocker).
