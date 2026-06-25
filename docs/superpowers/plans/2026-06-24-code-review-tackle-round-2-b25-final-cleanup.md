---
bundle: B25
name: Final cleanup + summary.md refresh
severity: —
stories: 0
m_effort: 0
main_plan: 2026-06-24-code-review-tackle-round-2.md
---

# B25 — Final cleanup + summary.md refresh

> **For agentic workers:** Use the **Common Workflow** from the main plan. No TDD; this is documentation bookkeeping.

**Bundle summary:** Update `docs/code_review/summary.md` with the final Round 2 tally, re-grade the themes, and update the `last_updated_commit`. No code changes.

**Expected commits:** 1-2.

---

## Tasks

### Task 1: Update `docs/code_review/summary.md`

**Files:** `docs/code_review/summary.md` (modify only).

**Change:** update the YAML front-matter:
- `last_updated_commit: <HEAD SHA on fix/code-review-tackle-2>`
- `last_staleness_scan: {commit: <HEAD SHA>, date: 2026-06-24}`

Update the per-theme grades table:
- Recount the open/in-progress/stale stories per theme.
- Apply the grade rubric (≤2 medium = A, 3-5 medium = B, 6-9 medium = C, ≥10 medium = D; 0 high = greenfield-clean, ≥1 high = grade cap at C).
- Update the "Grade changes vs e544584" line to reflect the post-Round 2 state.

Update the "Top concerns" list (10 most important open stories; should be empty or near-empty after Round 2).

Update the "Quick wins" list (story ID, severity, effort, theme file path).

Update the "Story counts" table:
- `open | <count>` (was 130 pre-Round 1; 121 post-Round 1; expect ~0 post-Round 2)
- `verified | 51 + 15 (Round 1) + 123 (Round 2) = 189`
- `fixed | 2 + 0 = 2`
- `stale | 0 + 3 (SEC-011, OBS-007, OBS-009) = 3`

Update the "Changes since last review" paragraph to describe Round 2's 25 bundles.

Update the "Last orientation snapshot" with any new entry points (e.g. new `WorkerSettings` fields from B5, new typed exceptions from B1).

**Test:** no new test; the YAML parses as valid front-matter.

**Commit:** `docs(code-review): record Round 2 verification (25 bundles, 123 stories)`.

---

### Task 2: Update `docs/superpowers/specs/2026-06-24-code-review-tackle-round-2-design.md` with final tally

**Files:** `docs/superpowers/specs/2026-06-24-code-review-tackle-round-2-design.md` (modify only).

**Change:** add a "Round 2 outcome" section at the bottom with:
- Total commits landed.
- Total bundles landed (should be 25).
- M-effort stories that needed iteration (split into multiple commits, deferred to a follow-up story, or surfaced to user).
- Stories that were `partial` or `not-addressed` in the correctness pass.
- Any cross-cutting follow-up items for Round 3.

**Test:** no new test.

**Commit:** covered by Task 1's commit (or a separate `docs(specs): record Round 2 outcome` commit).

---

## Bundle summary

- **Stories:** 0 (bookkeeping only).
- **Commits:** 1-2.
- **Cross-bundle:** none. This is the final bundle; after it lands, the round is complete.
- **Surface to user if:** the grade re-computation produces surprising results (e.g. a theme that's still C despite all the work, or new issues found by the final `just validate`).
