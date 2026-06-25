---
bundle: B24
name: Auth + remaining SEC + stale bookkeeping
severity: LOW
stories: 5
m_effort: 1
main_plan: 2026-06-24-code-review-tackle-round-2.md
---

# B24 — Auth + remaining SEC + stale bookkeeping (SEC-005, -019, -011, OBS-007, OBS-009)

> **For agentic workers:** Use the **Common Workflow** from the main plan. SEC-005 is M-effort; SEC-019 is S-effort; the 3 stale items are 1-line YAML updates.

**Bundle summary:** Add token-based auth to the API write routes, sanitise the edge 500 body, and fix the stale-bookkeeping items that Round 1's doc-staleness pass flagged but never updated.

**Expected commits:** 3-4.

---

## Tasks

### Task 1: SEC-005 (M) — Job submission/listing/capabilities routes require no authentication

**Story:** `docs/code_review/operations.md` § SEC-005 (LOW, M effort).

**Files:**
- Modify: `src/acheron/shell/api/routes.py` (or wherever the routes are).
- Modify: `src/acheron/shell/config.py` (add `Settings.api.auth_token: str | None = None`).
- Modify: `src/acheron/shell/api/deps.py` (add `verify_token` FastAPI dependency).
- Test: `tests/shell/api/test_auth.py` (new).

**Design:**

```python
# In config.py
class ApiSettings(BaseSettings):
    auth_token: str | None = None  # read from ACHERON_API_AUTH_TOKEN


# In deps.py
def verify_token(settings: Annotated[Settings, Depends(get_settings)]) -> None:
    if settings.api.auth_token is None:
        return  # dev mode (no auth)
    raise HTTPException(401, "API auth not yet implemented; configure auth_token")


# In routes.py
@router.post("/jobs", dependencies=[Depends(verify_token)])
async def submit_job(...): ...
```

**Important note for the M-effort design:** the simplest first cut is to add the `verify_token` dependency that always raises 401 in production (forcing the operator to set `auth_token` and implement a real check), and to no-op in dev (when `auth_token` is None). The full implementation (HMAC validation, JWT, etc.) is a follow-up story in Round 3.

**Test:** 1 test asserting that POST `/jobs` without auth returns 401 when `auth_token` is set, and 200 (or the existing 200) when not set.

**Commit:** `feat(SEC-005): add token-based auth dependency to API write routes (stub: 401 unless auth_token configured)`.

---

### Task 2: SEC-019 — edge `/execute` multipart branch returns 500 body with `error=str(exc)`

**Files:** `src/acheron/worker_sdk/_edge_http.py`; test.

**Change:** same as B8's SEC-012 (sanitise to `{exc_class}: <error>`), but for the multipart branch specifically.

**Test:** mock the multipart branch to raise `RuntimeError`; assert the 500 body does NOT contain the raw exception message.

**Commit:** `fix(SEC-019): sanitise exception messages in edge /execute multipart 500 body`.

---

### Task 3: SEC-011 — bookkeeping: `status: ?` → `status: stale`

**Files:** `docs/code_review/operations.md` (only).

**Change:** find the SEC-011 story; set `status: stale` in its YAML. Add a one-line comment to the story explaining the round-1 doc-staleness finding.

**Test:** no new test.

**Commit:** `docs(code-review): mark SEC-011 stale (resolved by Bundle 1 in Round 1)`.

---

### Task 4: OBS-007 — bookkeeping: `status: ?` → `status: stale`

**Files:** `docs/code_review/operations.md` (only).

**Change:** same as Task 3 for OBS-007 (was fixed in Round 1's Bundle 2 as a side-effect of OBS-010).

**Commit:** `docs(code-review): mark OBS-007 stale (resolved by OBS-010 in Round 1)`.

---

### Task 5: OBS-009 — bookkeeping: `status: ?` → `status: stale`

**Files:** `docs/code_review/operations.md` (only).

**Change:** same as Task 3 for OBS-009 (was fixed in Round 1's Bundle 2 as a side-effect of OBS-010).

**Commit:** `docs(code-review): mark OBS-009 stale (resolved by OBS-010 in Round 1)`.

---

## Bundle summary

- **Stories:** 5 (1 M-effort: SEC-005; 1 S-effort: SEC-019; 3 stale-bookkeeping).
- **Commits:** 3-4 (the 3 bookkeeping items can share 1 commit; SEC-005 and SEC-019 are 1 each).
- **Cross-bundle:** SEC-005's auth design depends on B4's CFG-003 (`ACHERON_OPEN_REGISTRATION`). Land B4 first or coordinate.
- **Surface to user if:** the dev-mode no-op behaviour is not desired (the user may want auth to be mandatory from the start).
