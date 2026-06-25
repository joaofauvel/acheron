---
bundle: B7
name: Exception handling A — typed errors
severity: MEDIUM
stories: 6
m_effort: 0
main_plan: 2026-06-24-code-review-tackle-round-2.md
---

# B7 — Exception handling A (EXC-004, -005, OBS-005, -006, -008, SEC-013)

> **For agentic workers:** Use the **Common Workflow** from the main plan. Each task is S-effort with coarse detail. Read the full story text in `docs/code_review/code-quality.md`, `operations.md` before implementing.

**Bundle summary:** Replace bare `BaseException` catches with typed errors, add log lines to silent-swallow sites, move RunPod API key to a header. All small surgical fixes.

**Expected commits:** 4-5.

---

## Tasks

### Task 1: EXC-004 + OBS-008 — `create_worker_app` lifespan catches bare `BaseException` for eager price refresh

**Files:** `src/acheron/worker_sdk/app.py`; test.

**Change:** narrow the `except` to `(httpx.HTTPError, OSError, ValueError, KeyError)`. Log `logger.exception(...)` with `endpoint_id`. Re-raise as `WorkerError` only if it's blocking startup; otherwise log and continue (current behaviour is continue).

**Test:** mock the price refresh to raise `httpx.HTTPError`; assert the lifespan continues without re-raising. Mock to raise `BaseExceptionGroup`; assert it propagates.

**Commit:** `fix(EXC-004, OBS-008): narrow BaseException catch in create_worker_app lifespan; log with context`.

---

### Task 2: EXC-005 — `_edge_http._dispatch` catches bare `BaseException` for handler failures

**Files:** `src/acheron/worker_sdk/_edge_http.py`; test.

**Change:** narrow to `Exception`. Let `BaseExceptionGroup` and `KeyboardInterrupt` propagate. Log `logger.exception(...)` with the handler name.

**Test:** mock the handler to raise `RuntimeError`; assert `_dispatch` logs and returns a 500. Mock to raise `KeyboardInterrupt`; assert it propagates.

**Commit:** `fix(EXC-005): narrow BaseException catch in _edge_http._dispatch; log handler name`.

---

### Task 3: OBS-005 — health providers swallow `(httpx.HTTPError, OSError)` silently

**Files:** `src/acheron/shell/health_providers.py`; test.

**Change:** before returning the cached/safe value, call `logger.warning(...)` with `provider_name`, `endpoint`, `exc_class`, and (if applicable) the HTTP status.

**Test:** mock the provider to raise `httpx.HTTPError`; assert a `WARNING`-level log line is emitted with `provider_name` and `endpoint` in the message.

**Commit:** `fix(OBS-005): log health-provider failures with provider_name and endpoint before falling back`.

---

### Task 4: OBS-006 — `RunPodClient` and `RunPodPrice` swallow transport / API errors with no log line

**Files:** `src/acheron/worker_sdk/_runpod_client.py`; `src/acheron/worker_sdk/pricing.py`; test.

**Change:** wrap the `asyncio.to_thread(...)` calls in `try/except` that logs `logger.exception(...)` with `endpoint_id` and `exc_class` before re-raising. (Round 1's CORR-014 already added a log for the FAILED-status path; this completes the gap on the exception path.)

**Test:** mock the endpoint to raise `httpx.HTTPError`; assert a log line is emitted with `endpoint_id` in the message.

**Commit:** `fix(OBS-006): log RunPod client and pricing failures with endpoint_id and exc_class`.

---

### Task 5: SEC-013 — `RunPodPrice` sends API key as URL query parameter

**Files:** `src/acheron/worker_sdk/pricing.py`; test.

**Change:** move the API key from `params={"api_key": ...}` to `headers={"Authorization": f"Bearer {api_key}"}`. Update the test mock's expected URL to NOT include the key as a query param. **Rotate the leaked API key in real RunPod accounts if applicable** (out of code scope; surface to user).

**Test:** patch httpx to capture the request; assert the URL has no `api_key=` param and the `Authorization` header is set.

**Commit:** `fix(SEC-013): move RunPod API key from URL query param to Authorization header`.

---

## Bundle summary

- **Stories:** 6 (all S).
- **Commits:** 4-5.
- **Cross-bundle:** B8 covers more observability (lower severity).
- **Surface to user if:** the RunPod API doesn't actually accept the Bearer token (verify with RunPod docs before committing).
