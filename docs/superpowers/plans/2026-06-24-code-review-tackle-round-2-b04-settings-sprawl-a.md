---
bundle: B4
name: Settings sprawl A — loaders + env vars
severity: MEDIUM
stories: 6
m_effort: 0
main_plan: 2026-06-24-code-review-tackle-round-2.md
---

# B4 — Settings sprawl A (CFG-003, -004, -005, -006, CORR-010, -011)

> **For agentic workers:** Use the **Common Workflow** from the main plan. Each task is S-effort with coarse detail. Read the full story text in `docs/code_review/architecture.md` and `docs/code_review/correctness.md` before implementing.

**Bundle summary:** Consolidate env-var reads into the project's settings loaders. Touches `src/acheron/shell/config.py` and a handful of consumers (`deps.py`, `transports/grpc.py`, `transports/http.py`, `worker_sdk/_runpod_client.py`, `worker_sdk/app.py`, `worker_sdk/cli.py`).

**Expected commits:** 4-5 (one per logical group; some stories share a fix).

---

## Tasks

### Task 1: CFG-003 — `ACHERON_OPEN_REGISTRATION` read directly in `deps.py`

**Files:** `src/acheron/shell/api/deps.py`; `src/acheron/shell/config.py`; test.

**Change:** add `open_registration: bool = False` to `OrchestratorSettings` (or a new `ApiSettings`); read `ACHERON_OPEN_REGISTRATION` via the settings loader. Update `deps.py` to read from `Settings` instead of `os.environ.get`.

**Test:** set `ACHERON_OPEN_REGISTRATION=1` via env, load `Settings`; assert `settings.api.open_registration is True`.

**Commit:** `fix(CFG-003): route ACHERON_OPEN_REGISTRATION through Settings loader`.

---

### Task 2: CFG-004 — Orchestrator mutates `Settings.orchestrator.data_dir` in-place from 2 call sites

**Files:** `src/acheron/shell/orchestrator.py` (the 2 call sites); test.

**Change:** each call site should build a fresh `Settings` with the override rather than mutating. E.g. `Settings(**settings.model_dump() | {"orchestrator": settings.orchestrator.model_copy(update={"data_dir": tmp_path})})` or similar.

**Test:** existing orchestrator tests with `tmp_path` should still pass; add 1 test asserting that the original `settings` is not mutated.

**Commit:** `fix(CFG-004): stop mutating Settings.orchestrator.data_dir in place`.

---

### Task 3: CFG-005 + CORR-010 — `${VAR}` env-var expansion silently substitutes unset vars as empty strings

**Files:** `src/acheron/shell/config.py` (the env-var expansion function); test.

**Change:** replace the silent-substitution behaviour with explicit failure on unset. The current pattern matches `${VAR}` and substitutes; the new pattern is `${VAR}` → raise `ValueError` (or a new typed `UnsetEnvVarError`) with the var name. The `:-default` syntax (`${VAR:-default}`) still works for optional vars.

**Test:** 
- Set `MY_VAR=hello`; assert `${MY_VAR}` → `hello`.
- Don't set `MY_VAR`; assert `${MY_VAR}` raises.
- Set `MY_VAR=hello`; assert `${MY_VAR:-default}` → `hello`.
- Don't set `MY_VAR`; assert `${MY_VAR:-default}` → `default`.

**Commit:** `fix(CFG-005, CORR-010): raise on unset env-var expansion; preserve :-default syntax`.

---

### Task 4: CORR-011 — env-var expansion pattern only matches uppercase variable names

**Files:** same as Task 3.

**Change:** the current regex is `[A-Z_][A-Z0-9_]*`; change to `[A-Za-z_][A-Za-z0-9_]*` (allow lowercase too).

**Test:** `${my_var}` should now expand if `my_var` is set in the env.

**Commit:** `fix(CORR-011): accept lowercase variable names in env-var expansion`.

---

### Task 5: CFG-006 — env vars read outside the project's settings loaders (5 new sites in transports + worker_sdk)

**Files:**
- `src/acheron/shell/transports/grpc.py` (line ~52: `ACHERON_DATA_DIR`)
- `src/acheron/shell/transports/http.py` (line ~54: same)
- `src/acheron/worker_sdk/_runpod_client.py` (line ~45: `ACHERON_WORKER__RUNPOD_BASE_URL`)
- `src/acheron/worker_sdk/app.py` (line ~57: `WORKER_HOST`)
- `src/acheron/worker_sdk/cli.py` (line ~72: `ACHERON_WORKER__LOG_LEVEL`)

**Change:** add `data_dir: Path` to `Settings` (already there) and pass it explicitly to the transports; add the missing fields to `WorkerSettings` (`runpod_base_url`, `worker_host`, `log_level`); change `extra="forbid"` to `extra="ignore"` only if needed for the new fields.

**Test:** `grep -n 'os.environ.get' src/acheron/` should return only the settings loaders. The existing tests on transports + worker_sdk should still pass.

**Commit:** `fix(CFG-006): remove 5 direct env-var reads in transports and worker_sdk; route through Settings/WorkerSettings`.

---

## Bundle summary

- **Stories:** 6 (all S).
- **Commits:** 4-5 (Tasks 3+4 share the env-var expansion function and should land together; Task 5 may be 1 commit per file or 1 commit for all 5 sites).
- **Cross-bundle:** CFG-005 + CORR-010 (Task 3) and CORR-011 (Task 4) are tightly coupled — same function, same tests, same commit.
- **Surface to user if:** `extra="forbid"` change on `WorkerSettings` breaks an existing test, or the `data_dir` plumbing through transports needs a new constructor arg on multiple classes.
