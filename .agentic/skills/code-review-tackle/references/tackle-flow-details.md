# Tackle Flow Details

Reference for `code-review-tackle/SKILL.md`. Defines selection, planning, implementation, commit, and PR behavior.

## Story selection

Args (passed by the user when invoking the skill):

- `<story-id>` (positional, repeatable): explicit story IDs, e.g., `ARCH-007 EXC-003`
- `--theme <prefix>`: all stories with this prefix (e.g., `--theme TYPE`)
- `--bundle <a-f>`: all stories in this bundle (e.g., `--bundle c`)
- `--severity <critical|high|medium|low>`: filter to this severity or higher
- `--status <state>`: filter to this status (default: `open`)
- `--pr`: open or update a PR after committing

If no positional IDs and no flags, default to `--status open --severity high` (top of the queue).

## Pre-flight staleness check

For each selected story:

- Read the cited files at HEAD.
- If the cited code is gone or materially changed → mark the story `stale` (in a `docs(code-review)` commit) and exclude from this tackle run.
- Surface the exclusion to the user with a suggestion to run `code-review-update` first.

## Plan vs trivial decision

For each selected story:

- If the story is trivial (single small file change, ≤30 lines of diff expected, no new abstractions): proceed inline.
- Otherwise: invoke `superpowers:writing-plans` to produce a plan, then proceed.

For batches of 2+ independent stories: invoke `superpowers:dispatching-parallel-agents` for plan + implementation. For dependent stories: serialize.

## Implementation

For each story (or independent batch):

- Behavior changes (new logic, new return type, new code path) → use `superpowers:test-driven-development`.
- Pure refactor with no behavior change → no TDD; verify behavior preservation via existing tests.
- New tests required by the story's `verification` field: write them first.

## Verification gate

Run per `verification-gate.md`. Pass required to proceed.

## Update review files

After verification passes:

- Set `status: fixed` on the tackled story (or stories). (Status advances to `verified` later, after BOTH subagent passes succeed — see steps 7 and 8 below.)
- Append `"pending"` to `fixed_in` (consolidator will resolve to SHA on next `update` or `tackle` run).
- Update `last_verified_at` to `{commit: <HEAD-to-be>, date: <YYYY-MM-DD>}`.

## Correctness subagent pass

Dispatch per `subagent-prompts/correctness-pass.md`. Receives the story content + the staged diff.

- `addressed` → continue.
- `not-addressed` → revert story to `status: in-progress`, surface to user with justification, abort.
- `partial` → ask user whether to continue (story stays `fixed`; the caveat is added to the story's per-theme narrative paragraph in `docs/code_review/summary.md` and the atomic commit proceeds) or revert to `in-progress`. The partial fix is staged but NOT yet committed at this point — the user decides whether to keep the staged changes or discard them.

## Doc-staleness subagent pass

Dispatch per `subagent-prompts/doc-staleness-pass.md`. Receives touched files + matching stories.

Apply the returned actions:

- `still-present` → locate story by `id`; locate the `files[]` entry matching `path`; update its `lines` to `new_lines`. Update story's `last_verified_at`.
- `mark-stale` → locate story by `id`; set `status: stale` for that story.

All review file changes are staged for the same commit as the code change.

## Atomic commit

```bash
git add <code files> docs/code_review/<changed bundle files>.md
git commit -m "fix(<STORY-ID>): <one-line summary from the story title>"
```

For multi-story tackle runs: ONE commit PER STORY. Order: independent stories interleave by selection order; dependent stories commit in dependency order.

The story's `status: verified` is set in the file BEFORE the atomic commit if both subagent passes succeeded, so the committed state captures the verified status directly. (`pending` placeholders in `fixed_in` are resolved later by the consolidator on the next `update` or `tackle` run.)

## PR behavior (`--pr` flag)

If `--pr` is passed:

### Safety checks

- Refuse if branch ∈ {`main`, `master`, `develop`}. Surface error.
- Refuse force-push. Use `git push` without `--force` or `--force-with-lease`.
- If branch has no upstream, set it: `git push --set-upstream origin <branch>`.

### Push

```bash
git push  # use git push -u origin <branch> the first time on a new branch
```

### Open or update PR

Check for existing PR: `gh pr view --json number,state 2>/dev/null`. If exists and open, update; otherwise create.

PR template:

```
Title: fix(code-review): <bundle-or-summary> (<STORY-ID-1>, <STORY-ID-2>, ...)

## Stories addressed
- <STORY-ID-1> — <title>
- <STORY-ID-2> — <title>

## Verification
- `just validate` passed at <head-short-sha>
- Correctness pass: addressed (per story)
- Doc-staleness pass: refreshed `last_verified_at` on N stories; marked M as stale

## Review entries
- docs/code_review/<bundle>.md (<STORY-IDs>)
```

Use `gh pr create` or `gh pr edit` accordingly. Pass body via HEREDOC for formatting.

### PR exception to atomic commit rule

After PR opens, append `"PR#<n>"` to `fixed_in` for each story. This is a separate `docs(code-review): record PR#<n>` commit — the only tolerated deviation from the one-commit-per-story rule.
