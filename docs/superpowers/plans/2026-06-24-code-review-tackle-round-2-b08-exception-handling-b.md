---
bundle: B8
name: Exception handling B — observability
severity: LOW
stories: 5
m_effort: 0
main_plan: 2026-06-24-code-review-tackle-round-2.md
---

# B8 — Exception handling B — observability (EXC-003, OBS-003, SEC-006, -010, -012)

> **For agentic workers:** Use the **Common Workflow** from the main plan. Each task is S-effort with coarse detail. Read the full story text in `docs/code_review/code-quality.md`, `operations.md` before implementing.

**Bundle summary:** Add log lines, sanitise exception strings, and add structured logging context. Lower-severity observability fixes.

**Expected commits:** 3-4.

---

## Tasks

### Task 1: EXC-003 — `HealthMonitor._handle_failure` catches bare `Exception` from the platform probe

**Files:** `src/acheron/shell/health.py`; test.

**Change:** narrow `except Exception` to `(httpx.HTTPError, OSError, ValueError)`. Log `logger.warning(...)` with `worker_id` and `provider_name` before falling back.

**Test:** mock the platform probe to raise `RuntimeError`; assert it's NOT caught (propagates as the platform probe bug). Mock to raise `httpx.HTTPError`; assert the fallback path is taken and a log line is emitted.

**Commit:** `fix(EXC-003): narrow Exception catch in HealthMonitor._handle_failure; log context`.

---

### Task 2: OBS-003 — logs are free-form with no structured fields or trace correlation

**Files:** `src/acheron/shell/logging_setup.py` (or wherever logging is configured); scattered call sites.

**Change:** introduce a `contextvars.ContextVar` for `job_id` and `request_id`. Wrap the FastAPI middleware to set them; the orchestrator's `submit_job` to set `job_id`. All `logger.info/warning/error(...)` calls in the touched modules can use `extra={"job_id": job_id, ...}` (stdlib logging supports this). If `structlog` is already in `pyproject.toml`, switch to it; otherwise stay with stdlib + `extra=`.

**Test:** a test that calls a function that logs; assert the log record's `extra` dict contains the expected `job_id`.

**Commit:** `feat(OBS-003): add job_id/request_id contextvars and structured logging`.

**Note:** this is the only "feat" commit in the round. The story text uses "fix" terminology; the change adds a new logging feature, not just a bug fix.

---

### Task 3: SEC-006 — raw exception strings exposed in `PlanResult.errors` via OBS-004 fix

**Files:** the OBS-004 fix site (find via `git log -S "PlanResult.errors"` or grep `PlanResult`); test.

**Change:** sanitise the error string to `{exc_class_name}: {first_line}`. Strip traceback fragments (lines starting with `File ` or `Traceback`).

**Test:** raise a `RuntimeError("secret stuff\n  File '/etc/passwd'")`; assert the resulting `PlanResult.errors` entry is `RuntimeError: secret stuff` (no `File ` line).

**Commit:** `fix(SEC-006): sanitise exception strings in PlanResult.errors`.

---

### Task 4: SEC-010 — worker `last_error` exposed via unauthenticated `/workers` endpoint

**Files:** `src/acheron/shell/api/routes.py` (or wherever `/workers` is); test.

**Change:** either (a) add a `Depends(verify_token)` to the `/workers` endpoint (B24's SEC-005 is the cross-cutting auth fix — if you want to land SEC-010 first, add a separate auth helper), or (b) scrub the `last_error` field from the response when no auth is provided.

**Test:** GET `/workers` with no auth; assert `last_error` is `None` or absent. With a valid token, assert `last_error` is included.

**Commit:** `fix(SEC-010): hide worker last_error from unauthenticated /workers responses`.

---

### Task 5: SEC-012 — edge `/execute` returns raw `str(exc)` in 500 body

**Files:** `src/acheron/worker_sdk/_edge_http.py` (the 500 handler); test.

**Change:** return `{exc_class}: {sanitised_msg}` instead of `str(exc)`. Add a `logger.exception(...)` with the full traceback for the operator.

**Test:** mock the handler to raise `RuntimeError("secret DB password=foo")`; assert the 500 body is `RuntimeError: secret DB password=foo` — wait, that's the same. Sanitise further: return `{exc_class}: <error>` (no message) or strip common secret patterns. The test should assert the body does NOT contain `password=foo`.

**Commit:** `fix(SEC-012): sanitise exception messages in edge /execute 500 body`.

---

## Bundle summary

- **Stories:** 5 (all S).
- **Commits:** 3-4 (OBS-003 may be a bigger commit than the others; consider isolating it).
- **Cross-bundle:** SEC-012 is the same anti-pattern as B7's OBS-005/-006. The fixes are independent (different code paths) but commit messages can cross-reference.
- **Surface to user if:** `structlog` is needed (would require a dep change — out of scope for this story per the spec note in B8 task 2).
