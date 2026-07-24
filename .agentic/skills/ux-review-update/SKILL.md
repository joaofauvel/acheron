---
name: ux-review-update
description: Refresh the Acheron UX review rubric (docs/ux_review/*.md) against the current commit. Re-resolves file:line references, re-exercises journeys, marks stale stories, and appends new findings under next-available IDs. Use after a merge to master, or when the user invokes --force. Refuses if no docs/ux_review/summary.md exists on the branch.
---

# ux-review-update

Refreshes the rubric against the current commit while preserving the `verified`, `wontfix`, and `partial` state machines. Per the spec's §9.3, performs two independent checks:

1. **File/line re-resolution.** Each story's `files[].path` and `files[].lines` are re-resolved against HEAD. Drift → mark `stale` (concern unchanged, code changed) or `obsolete` (concern no longer valid, code or spec changed).
2. **Journey re-exercise.** Each story's `user_journey` is re-exercised against its harness (`just sim-run` / `just first-run`). For journeys without a harness, a fresh-context subagent re-reads the `user_facing_surface` and asserts the journey is still satisfiable. Journey drift → mark `stale` and append a `drift_note:` line.

## When to run

- After a merge to master (CI hook).
- When the user invokes `--force` to refresh manually.
- Never run on a feature branch that has unmerged rubric changes (run on a clean master snapshot).

## Pre-flight

1. Check `ls docs/ux_review/summary.md`; abort if absent (the user should run `ux-review-perform` first).
2. Read `docs/ux_review/summary.md`'s frontmatter for `last_updated_commit`; compute `git diff <last_updated_commit>..HEAD --name-only` to identify files that changed.
3. Read the spec's §9.3 for the two-check rule and the `discovered_via` preservation rule.

## Subagent dispatch

Spawn 3 subagents, one per theme. Each receives:
- The theme file content.
- The list of files that changed (filtered to the theme's typical scope per §2).
- An instruction: for each story, (a) re-resolve file:line, (b) re-exercise journey, (c) classify drift (none / stale / obsolete), (d) preserve `discovered_via` lists, (e) update `fixed_in` from `git log --grep="(<id>)"`.
- New findings discovered during the refresh: append under the theme's next-available ID with `discovered_via: [code-review]` (the subagent's evidence is the diff).

## Output

- Theme files updated in place.
- `summary.md` updated: `last_updated_commit: <HEAD>`; per-theme grade re-computed from open stories; status counts refreshed.
- Commit: `docs(ux-review): refresh at <head-sha>`.

## Anti-patterns

- Do NOT modify `verified`/`wontfix` stories (mirror the code-review refresh rule).
- Do NOT lose `discovered_via` lists across refreshes (a story originally surfaced via `code-review` and later confirmed via `simulation` keeps both, with the strongest first).
- Do NOT re-resolve a `verified` story's `verified_in` or `last_verified_at` (the verification is anchored to the original commit).
