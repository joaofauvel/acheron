# Correctness Pass — Subagent Prompt

Used by `code-review-tackle` after the verification gate passes. Fresh-context subagent receives the story and the diff, returns a verdict.

## Subagent type

Use the current harness's default capable reviewer worker with read-only tools for file reads and `git diff`/`git show`.

## Prompt template

```
You are verifying whether a code-review story has been addressed by a specific commit.

## The story

[Inline the full story content: ID, title, YAML metadata, Issue, Why it matters, Recommendation, Verification.]

## The change

Branch: <branch>
Commit: <sha>

Diff:
[Inline the output of `git show <sha>` for the files cited in the story's `files[].path` list. If the full commit is large (>500 lines of diff total), prefer `git show <sha> -- <cited-paths>` to scope the diff to just the cited files; include a one-line summary of any OTHER changed files at the top so the subagent can spot scope creep. If `<sha>` is a range, use `git diff <parent>..<sha> -- <cited-paths>` instead.]

## Your task

Answer: does this change actually address the story?

Consider:
- Does the diff modify the cited files at the cited locations (or equivalent locations after refactor)?
- Does the change implement the recommendation, or a reasonable alternative?
- Are there obvious regressions in the diff (e.g., new bare exceptions, new untyped code, new test gaps)?

## Output

Emit a single JSON object:

{
  "verdict": "addressed" | "not-addressed" | "partial",
  "justification": "one paragraph explaining the verdict, citing specific diff lines"
}

- "addressed": the change implements the recommendation or an equivalent fix; no obvious regression in the diff.
- "not-addressed": the change does not address the story (e.g., touched the wrong file, only added a comment, did not fix the cited issue).
- "partial": the change addresses some but not all aspects of the story (e.g., fixed 3 of 5 sites in a bundled story).

Do NOT speculate beyond the diff. Do NOT propose follow-up work — that is for the main agent.
```

## Output handling

The main agent reads the JSON:

- `addressed` → story stays at `status: fixed`; proceed to doc-staleness pass. Status advances to `verified` only after BOTH subagent passes succeed (just before the atomic commit at step 9 of the tackle flow).
- `not-addressed` → revert story to `status: in-progress`, surface to user with the justification, abort the tackle run (the in-progress code is staged but unconcommitted at this point — the user decides whether to keep or discard the staged changes).
- `partial` → surface to user with justification; ask whether to continue (status remains `fixed` with a caveat to be added to the story's per-theme narrative paragraph in `docs/code_review/summary.md`) or revert to `status: in-progress`. The partial fix is staged but uncommitted at this point.

## Why fresh context

The implementation conversation has full context of the work done. A fresh subagent reads the story and diff cold — it cannot rationalize that "the recommendation was infeasible" or "this is good enough" based on a discussion it didn't see. This produces an unbiased verdict.
