---
name: code-review-update
description: Refresh the existing `docs/code_review/` artifact against current code — re-resolve cited line ranges, mark vanished issues as stale, append new findings under next-available IDs, re-grade themes. Use when the codebase has evolved since the last `last_updated_commit` in `docs/code_review/summary.md`.
---

# code-review-update

Refreshes `docs/code_review/`. Same bundle subagents as `code-review-perform`, but they receive the existing review content + the diff since `last_updated_commit` instead of the full codebase.

## When to invoke

- "refresh the code review"
- "update code review"
- "re-scan codebase for new issues"
- `docs/code_review/` exists and HEAD has advanced since `last_updated_commit`

## When NOT to invoke

- No existing `docs/code_review/` → use `code-review-perform`
- Working through stories → use `code-review-tackle`
- HEAD == `last_updated_commit` (no diff to scan)

## Prerequisites

- `docs/code_review/` exists with valid YAML frontmatter on `summary.md`.
- Working tree clean OR unrelated changes acknowledged.
- Branch is NOT `main`/`master`/`develop`.
- `git` available.

## Flow

### 1. Pre-flight

- Read `docs/code_review/summary.md` frontmatter. Extract `last_updated_commit`. If the key is missing or empty, abort (treat as malformed frontmatter; see Failure modes).
- Run `git rev-parse HEAD` and `git branch --show-current`. Refuse main/master/develop.
- Compute diff: `git diff <last_updated_commit>..HEAD --name-only`. If empty, exit with "no changes to scan."

### 2. Orientation delta brief

Generate the same orientation brief as `perform` (per `code-review-perform/references/orientation-prelude.md`), with an added "Changes since last review" section listing changed/added/removed top-level dirs and notable file count deltas.

### 3. Dispatch 6 parallel bundle subagents

Use `superpowers:dispatching-parallel-agents`. Same as `perform`, with these prompt additions:

- Existing per-bundle file content (the `docs/code_review/<bundle>.md`)
- Full patch output for files matching the bundle's path globs: `git diff <last_updated_commit>..HEAD -- <glob-matched-files>` (not just file names — subagents need to see the actual changes)
- Updated subagent task: per `code-review-perform/references/consolidator-instructions.md` "Update-mode immutability":
  - Re-resolve `files[].lines` for stories with `status: open | in-progress | stale` whose cited files appear in the diff
  - Mark `open`/`in-progress` stories as `stale` if cited code is gone or no longer matches
  - Append new findings under next-available IDs
  - **NEVER modify `fixed`/`verified`/`wontfix` stories** — regressions become NEW stories with `related: [<old-id>]`

Tools: read-only.

Wait for all 6.

### 4. Consolidation

Apply `code-review-perform/references/consolidator-instructions.md` in update mode:

- Merge subagent updates into existing files (preserve immutable stories)
- Assign new IDs as `(max-existing + 1)` per prefix
- Recompute grades
- Update `last_updated_commit`, `last_staleness_scan` on every modified file — preserve `initial_review_commit` verbatim
- Resolve any `pending` SHA placeholders by walking `git log`
- Update `summary.md`

### 5. Commit

```bash
git add docs/code_review/
git commit -m "docs(code-review): refresh at $(git rev-parse --short HEAD)"
```

## References

All shared docs live in `code-review-perform/references/`:

- `themes-taxonomy.md`
- `grading-rubric.md`
- `story-template.md`
- `orientation-prelude.md`
- `consolidator-instructions.md`
- `bundle-prompts/a-correctness.md` through `bundle-prompts/f-surface.md` (6 files)

## Failure modes

- Existing frontmatter malformed: surface error, abort.
- Subagent times out: skip its updates for this run, log warning.
- A `fixed`/`verified` story's cited code has regressed: do NOT modify the story; add a NEW finding (different ID) and link via `related`.
- Orientation delta brief exceeds 200 lines: truncate to 200, log warning.
- A `stale` story's cited issue has returned at a new location: do NOT auto-elevate to `open`. Mark `last_verified_at` per the doc-staleness pass; the user re-evaluates manually whether the story should reopen or get a new ID. This avoids silent reactivation of stories the user may have decided to leave stale.

## After

Hand off to `code-review-tackle` for newly-surfaced stories.
