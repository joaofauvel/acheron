---
name: ux-review-tackle
description: Tackle a single Acheron UX review story. Creates a worktree, implements the fix, validates against the per-surface gate, and opens a PR with the canonical PR body. Use when the user invokes `<story-id>` (positional) or `--bundle <id>,<id>,...` for grouped tackles.
---

# ux-review-tackle

Implements a single UX story end-to-end. The worktree flow mirrors `code-review-tackle` but the validation gate is per-`user_facing_surface` (per the spec's §9.2).

## Args

- `<story-id>` (positional, repeatable): the story ID to tackle (e.g., `DEPLOY-001`).
- `--bundle <id1>,<id2>,...`: group stories with the same `user_facing_surface` and non-conflicting `files` into one worktree.
- `--pr`: open a PR after the worktree is ready.

## Pre-flight

1. `ls docs/ux_review/summary.md`; abort if absent.
2. For each `<story-id>`, verify it exists in a theme file and is `status: open` (or `in-progress` for re-tackle).
3. Refuse if branch `ux-tackle/<story-id>-*` already exists (forces explicit rebase).
4. Read the story's `files[].path` and `journey_stage` to set the per-surface gate.

## Worktree

```sh
git worktree add -b ux-tackle/<story-id>-<slug> .worktrees/ux-tackle-<story-id> master
```

For `--bundle`: `git worktree add -b ux-tackle/<theme>-bundle-<N> .worktrees/ux-tackle-bundle-<N> master`.

## Plan

If the fix is trivial (≤30 LoC, single file, no new abstraction), implement inline. Else, run `superpowers:writing-plans` for a structured plan.

## Implement

Behavior changes via TDD. For `discovered_via: [simulation]` stories, the implementation MUST be paired with a new file under `sim/scenarios/` that references the story ID in a docstring (`STORY_REF: <id>`). For `discovered_via: [first-run]` stories, the new file lives under `tests/first_run/`.

## Validate

The per-surface gate (spec §9.2):

| Surface | Gate |
|---|---|
| `cli` | `just validate` |
| `dashboard` | `just validate` + first-run test step |
| `runpod-api` | `just validate` + sim scenario |
| `compose` | `just validate` + first-run test step |
| `quickstart` | first-run test step only |
| `certs` | first-run test step + manual cert-rotation check |
| `worker-image` | `just validate` + manual image build |
| `internal` | `just validate` |

Plus `just ux-validate` against the rubric. Plus, for sim/first-run stories, the corresponding scenario/test must pass.

## Atomic commit

One commit titled `fix(<STORY-ID>): <imperative summary>`. Includes the code change + the story file with `status: fixed`, `fixed_in: [pending]`, `last_verified_at: {commit: <pending>, date: <today>}`. Drive-by cleanups land in separate commits.

## PR body (if `--pr`)

```
Closes: <STORY-ID>
Journey: <verbatim user_journey from the story>
Evidence: <link to harness output or transcript path>
Rollback: <one-line revert or feature-flag story>
```

Plus, if the PR also closes a `related:` code-review story, add `Closes-CodeReview: <id>` to the commit message trailer.

## Post-merge

- Remove the worktree: `git worktree remove .worktrees/ux-tackle-<story-id> --force; git branch -d ux-tackle/<story-id>-<slug>`.
- The next `ux-review-update` resolves `fixed_in: [pending]` to the commit SHA whose scope includes the story ID.
- The story flips to `verified` when the harness artifact (or human `verified_by`) confirms the fix.

## Anti-patterns

- Do NOT mark a story `verified` with `verified_by: <pr-author>`. The `verified` step requires a second pair of eyes (or a harness artifact).
- Do NOT skip the per-surface gate. A `cli` PR that passes `ruff` but skips `mypy` is a `Verification` failure.
- Do NOT file a PR that includes a UX story and a code-review story without the `Closes-CodeReview:` trailer.
