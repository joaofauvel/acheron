---
bundle: B18
name: Type safety — stringly-typed responses
severity: LOW
stories: 3
m_effort: 0
main_plan: 2026-06-24-code-review-tackle-round-2.md
---

# B18 — Type safety — stringly-typed responses (TYPE-004, -005, MAINT-014)

> **For agentic workers:** Use the **Common Workflow** from the main plan. Each task is S-effort with coarse detail. Read the full story text in `docs/code_review/code-quality.md` before implementing.

**Bundle summary:** Replace `str`-typed status fields with the existing enums; drop 6 redundant no-op overrides. Quick wins.

**Expected commits:** 2-3.

---

## Tasks

### Task 1: TYPE-004 — `WorkerResponse.status` is stringly-typed despite a `WorkerStatus` enum existing

**Files:** `src/acheron/worker_sdk/schemas.py` (or wherever `WorkerResponse` is); test.

**Change:** change `status: str` to `status: WorkerStatus` (Pydantic v2 will validate the enum membership).

**Test:** `WorkerResponse(status=WorkerStatus.HEALTHY)` should work. `WorkerResponse(status="HEALTHY")` should also work (Pydantic coerces). `WorkerResponse(status="not-a-status")` should raise a validation error.

**Commit:** `fix(TYPE-004): use WorkerStatus enum in WorkerResponse.status`.

---

### Task 2: TYPE-005 — `JobResponse.status` and `total_cost_basis` are stringly-typed

**Files:** `src/acheron/shell/schemas.py` (or wherever `JobResponse` is); test.

**Change:** `status: JobStatus` (Pydantic enum); `total_cost_basis: Decimal` (Pydantic-validates the string-to-Decimal coercion).

**Test:** `JobResponse(status=JobStatus.COMPLETED, total_cost_basis="10.50")` should work; `total_cost_basis="not-a-number"` should raise.

**Commit:** `fix(TYPE-005): use JobStatus enum and Decimal for JobResponse status and cost fields`.

---

### Task 3: MAINT-014 — stub handlers redundantly override the ABC's default no-op `startup`/`shutdown`

**Files:** `stubs/_sdk_base/stub_handlers.py` (or wherever the stubs are); test.

**Change:** delete the 6 redundant overrides. The `WorkerHandler` ABC defaults (`async def startup() -> None: return` and `async def shutdown() -> None: return`) take over automatically.

**Test:** no new test; the existing stub handler tests should still pass.

**Commit:** `refactor(MAINT-014): drop redundant startup/shutdown overrides in stub handlers`.

---

## Bundle summary

- **Stories:** 3 (all S).
- **Commits:** 2-3 (Tasks 1+2 may be 1 commit if they're in the same file).
- **Cross-bundle:** none.
- **Surface to user if:** Pydantic v2's enum coercion is more or less strict than expected (e.g. `WorkerStatus("healthy")` may not match `WorkerStatus.HEALTHY` if the enum uses uppercase values only).
