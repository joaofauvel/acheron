---
bundle: B23
name: DOC + DX + PKG
severity: LOW
stories: 6
m_effort: 1
main_plan: 2026-06-24-code-review-tackle-round-2.md
---

# B23 — DOC + DX + PKG (DOC-003, -004, DX-003, PKG-002, -003, EXC-001)

> **For agentic workers:** Use the **Common Workflow** from the main plan. EXC-001 is M-effort (tenacity removal); the rest are S-effort doc/config cleanups.

**Bundle summary:** Consolidate configuration docs, add `granite_speech` to README, fix `just install` for new workspace members, drop dead `pyproject.toml` keys, align `cryptography` pins, and (M-effort) remove the unused `tenacity` dep + never-raised exceptions.

**Expected commits:** 4-5.

---

## Tasks

### Task 1: DOC-003 — Configuration docs drift across README, `.env.example`, and an undocumented dashboard

**Files:** `README.md`; `.env.example`; `docs/configuration.md` (new).

**Change:** introduce a single `docs/configuration.md` that lists every env var, its default, and where it's read. Update README and `.env.example` to defer to it.

**Test:** no new test; the docs should render.

**Commit:** `docs(DOC-003): consolidate env-var docs in docs/configuration.md`.

---

### Task 2: DOC-004 — README architecture tree, CI section, and Test paths omit `granite_speech`

**Files:** `README.md`; `.github/workflows/*.yml` (if it has a Test paths section).

**Change:** add `granite_speech` to the architecture tree, CI section, and Test paths.

**Test:** no new test.

**Commit:** `docs(DOC-004): add granite_speech to README architecture tree, CI section, and Test paths`.

---

### Task 3: DX-003 — `just install` doesn't install the new `workers/qwen3tts/` workspace member

**Files:** `Justfile` (or `just install` recipe).

**Change:** change `uv sync` to `uv sync --all-packages` (or `uv sync --all-extras --all-packages` if extras are used).

**Test:** the `just install` recipe should now install all workspace members.

**Commit:** `fix(DX-003): just install should use uv sync --all-packages`.

---

### Task 4: PKG-002 — `pyproject.toml` dead `root_package` key + duplicate `soundfile` dev entry

**Files:** `pyproject.toml`.

**Change:** remove the dead `root_package` key; remove the duplicate `soundfile` dev entry.

**Test:** `uv lock` should still work; `just validate` should still pass.

**Commit:** `chore(PKG-002): remove dead root_package key and duplicate soundfile dev entry`.

---

### Task 5: PKG-003 — `Dockerfile:39` pins `cryptography~=49.0` while `pyproject.toml:168` pins `~=46.0`

**Files:** `Dockerfile`; `pyproject.toml`.

**Change:** align both to the newer pin. Decide which version is correct (the newer `49.0` may have API changes) — surface to user if unsure.

**Test:** `just validate` should still pass.

**Commit:** `chore(PKG-003): align cryptography pin across Dockerfile and pyproject.toml`.

---

### Task 6: EXC-001 (M) — `tenacity` dependency is unused; `WorkerTimeoutError`/`PlanValidationError` are never raised

**Story:** `docs/code_review/code-quality.md` § EXC-001 (MEDIUM, M effort).

**Files:**
- Modify: `pyproject.toml` (remove `tenacity` from deps).
- Modify: `src/acheron/core/errors.py` (remove the never-raised exceptions, or wire them up).
- Modify: any code that imports them.
- Test: existing tests should still pass.

**Design (option 1 — remove):** `grep -rn tenacity src/` returns 0 hits; remove the dep. Remove the exception classes (or document them as future-use).

**Design (option 2 — wire up):** use `tenacity` in the orchestrator's retry logic (e.g. retry on transient RunPod errors). Use `WorkerTimeoutError` in the HTTP transport's timeout path; use `PlanValidationError` in `compile_plan`'s validation path.

**Recommendation:** option 1 (remove) is simpler and matches the AGENTS.md YAGNI rule. The exceptions can be re-added in a future story when they're actually used.

**Test:** existing tests should still pass. Add 1 test asserting `tenacity` is not in `pyproject.toml`'s deps.

**Commit:** `chore(EXC-001): remove unused tenacity dep and never-raised WorkerTimeoutError/PlanValidationError exceptions`.

---

## Bundle summary

- **Stories:** 6 (1 M-effort: EXC-001; 5 S-effort).
- **Commits:** 4-5.
- **Surface to user if:** option 1 (remove tenacity) breaks a downstream consumer; surface option 2 (wire up) for the user to choose.
