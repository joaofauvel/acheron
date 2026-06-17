---
name: code-review-perform
description: Generate the initial code review for this codebase — scan src/, tests/, models/, macros/, and tooling files across 17 theme prefixes in 6 parallel bundles, produce stories with stable IDs and grades, write to docs/code_review/, and commit. Use when no review exists at docs/code_review/, or with --rebuild for a full regeneration.
---

# code-review-perform

Generates the initial `docs/code_review/`.

## When to invoke

- "perform initial code review"
- "generate code review"
- "scan codebase for a structured review"
- No existing `docs/code_review/` directory, OR explicit `--rebuild` flag

## When NOT to invoke

- `docs/code_review/` already exists and you want to refresh it → use `code-review-update`
- Working through specific stories → use `code-review-tackle`

## Args

- `--rebuild`: force regeneration even if `docs/code_review/` exists. Existing files are overwritten; story IDs reset.

## Prerequisites

- Working tree clean (`git status` empty) OR all uncommitted changes are unrelated and explicitly acknowledged.
- Branch is NOT `main`, `master`, or `develop` (skill commits to current branch).
- `git` available. (No `gh`, `just`, or `poetry` invocations in this skill — those are used by `code-review-tackle`.)

## Flow

### 1. Pre-flight

- Run `git status`. If dirty, surface to user and ask for confirmation to proceed.
- Run `git rev-parse HEAD` and `git branch --show-current`. Refuse if branch ∈ {main, master, develop}.
- Check `docs/code_review/` exists. If yes and no `--rebuild`, abort with message directing user to `code-review-update`.

### 2. Codebase orientation prelude

Per `references/orientation-prelude.md`. Main agent generates the brief directly (no subagent recursion). Brief is ≤200 lines of markdown.

### 3. Dispatch 6 parallel bundle subagents

Use `superpowers:dispatching-parallel-agents`. Send 6 simultaneous subagent invocations, one per bundle:

For each bundle (a-correctness, b-architecture, c-code-quality, d-verification, e-operations, f-surface):

- Worker type: use the current harness's default capable reviewer worker.
- Prompt: contents of `references/bundle-prompts/<bundle>.md` + the orientation brief + `references/themes-taxonomy.md` + `references/grading-rubric.md` + `references/story-template.md`
- Tools: read-only file search, file reads, and shell inspection commands such as `find`/`wc`.
- Expected output: structured JSON per the bundle prompt's output schema

Wait for all 6 to complete.

### 4. Consolidation

Apply `references/consolidator-instructions.md`:

- Flatten and tag findings
- Dedup via `related:` cross-refs (no deletion)
- Assign IDs (perform mode: 001, 002, ... per prefix in severity-desc, file-path-asc order)
- Compute per-theme grades
- Write per-bundle files with frontmatter: `initial_review_commit: <HEAD SHA>`, `last_updated_commit: <HEAD SHA>`, `last_staleness_scan: { commit: <HEAD SHA>, date: <YYYY-MM-DD> }`
- Write `summary.md` with grade table, top concerns, quick wins, story counts, orientation snapshot

### 5. Commit

```bash
git add docs/code_review/
git commit -m "docs(code-review): initial review at $(git rev-parse --short HEAD)"
```

## References

- `references/themes-taxonomy.md` — list of all theme prefixes organized into 6 bundles
- `references/grading-rubric.md` — severity-count → letter grade
- `references/story-template.md` — YAML+prose story format
- `references/orientation-prelude.md` — codebase brief structure
- `references/consolidator-instructions.md` — dedup, IDs, grades, file writes
- `references/bundle-prompts/{a,b,c,d,e,f}-*.md` — per-bundle subagent prompts

## Failure modes

- Subagent times out or returns garbage: log warning, skip its findings, continue with others.
- Working tree dirty after `git add` but commit fails: surface git error, do not retry.
- File write to `docs/code_review/` fails: abort, do not partial-commit.
- Orientation prelude generation exceeds 200 lines: truncate to 200, log warning. Inflated briefs cost tokens in every subagent prompt.

## After

Hand off to `code-review-tackle` to start working through stories, or to `code-review-update` after enough code has changed to warrant refresh.
