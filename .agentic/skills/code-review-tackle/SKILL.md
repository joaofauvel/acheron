---
name: code-review-tackle
description: Work through one or more code-review stories from `docs/code_review/` — plan, implement (with TDD for behavior changes), verify with the project gate (`just validate` + `dbt parse`), run two fresh-context subagent passes (correctness + doc-staleness), and commit atomically with the story ID in scope. Optional `--pr` flag pushes the branch and opens or updates a PR. Use when fixing items from `docs/code_review/`.
---

# code-review-tackle

Executes the tackle flow.

## When to invoke

- "tackle ARCH-007"
- "fix the top high-severity stories"
- "work through the critical issues" (severity-driven)
- "work through CFG bundle"
- "implement EXC-003 and TYPE-012"

## When NOT to invoke

- No `docs/code_review/` exists → use `code-review-perform`
- Refreshing review against current code → use `code-review-update`

## Args

- `<story-id>` (positional, repeatable): explicit IDs, e.g., `ARCH-007 EXC-003`
- `--theme <prefix>`: all stories with this prefix
- `--bundle <a-f>`: all stories in this bundle
- `--severity <critical|high|medium|low>`: filter to this severity or higher
- `--status <state>`: filter to this status, where `<state>` is one of `open | in-progress | fixed | verified | stale | wontfix` (default: `open`)
- `--pr`: push branch and open/update a PR after committing

Default with no args: `--status open --severity high`.

## Prerequisites

- `docs/code_review/` exists with at least one story matching the selection.
- Working tree clean.
- Branch is NOT `main`/`master`/`develop`.
- `just`, `poetry`, `git`, `gh` available (`gh` only required when `--pr` is used).

## Flow

Per `references/tackle-flow-details.md`:

1. **Select** stories from `docs/code_review/` per args.
2. **Pre-flight staleness check** — exclude stale stories with a doc-only commit.
3. **Plan**: trivial → inline; otherwise → `superpowers:writing-plans`. Batch independent stories via `superpowers:dispatching-parallel-agents`.
4. **Implement** — `superpowers:test-driven-development` for behavior changes.
5. **Verification gate** per `references/verification-gate.md`: ruff fix/format + `just lint-strict` + `just type-check` + `just test` + `dbt parse`. Fail aborts.
6. **Update review files**: `status: fixed`, append `"pending"` to `fixed_in`, update `last_verified_at`.
7. **Correctness subagent pass** per `references/subagent-prompts/correctness-pass.md`. `addressed` continues; `not-addressed` reverts and aborts; `partial` asks user.
8. **Doc-staleness subagent pass** per `references/subagent-prompts/doc-staleness-pass.md`. Apply `still-present` (update `last_verified_at`/`files[].lines`) and `mark-stale` (set `status: stale`) actions.
9. **Atomic commit** with `fix(<STORY-ID>): <summary>`. One commit per story. After the commit, the story's in-memory status advances to `verified` (the consolidator resolves the SHA placeholder in `fixed_in` on the next `update` or `tackle` run).
10. **PR (if `--pr`)**: refuse main/master/develop, refuse force-push, push, open or update PR per template in `references/tackle-flow-details.md`. Append `"PR#<n>"` to `fixed_in` in a follow-up `docs(code-review)` commit.

## References

- `references/verification-gate.md` — exact gate commands and failure rules
- `references/tackle-flow-details.md` — selection, planning, implementation, commit, PR behavior
- `references/subagent-prompts/correctness-pass.md` — correctness verification subagent prompt
- `references/subagent-prompts/doc-staleness-pass.md` — staleness scan subagent prompt

Shared with other code-review skills:

- `../code-review-perform/references/themes-taxonomy.md`
- `../code-review-perform/references/grading-rubric.md`
- `../code-review-perform/references/story-template.md`

## Failure modes

- Pre-flight staleness check excludes all selected stories: surface, exit, suggest `code-review-update`.
- Verification gate fails: abort, story remains `status: in-progress`, no commit.
- Correctness pass returns `not-addressed`: revert story state to `in-progress`, abort, surface justification.
- PR push refused (branch is main/master/develop, force-push attempted): surface and exit.

## After

- Without `--pr`: user reviews commits and pushes/opens PR manually.
- With `--pr`: PR is open or updated with the latest tackled stories. A second commit (`docs(code-review): record PR#<n>`) appends the PR number to `fixed_in` for each tackled story — this is the only tolerated deviation from the one-commit-per-story rule.
- Periodically run `code-review-update` to resolve `pending` SHA placeholders and mark drift.
