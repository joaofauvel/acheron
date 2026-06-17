# Doc-Staleness Pass — Subagent Prompt

Used by `code-review-tackle` after the correctness pass. Fresh-context subagent receives the touched files + the full review content, returns staleness actions.

## Subagent type

Use the current harness's default capable reviewer worker with read-only tools.

## Prompt template

```
You are checking whether code-review stories that cite specific files are still accurate after a recent change.

## Touched files

[List of file paths touched by the recent commit.]

## Stories to check

For each file in the touched list, you will be given the stories from `docs/code_review/` that cite that file (any theme, any bundle). For each story:

- ID, title, YAML metadata
- Issue description
- Recommendation

[Inline the matching stories. Skip stories with status: fixed | verified | wontfix — those are immutable.]

## Your task

For each story (with status: open | in-progress | stale):

1. Read the cited file at the current state of the working tree.
2. Locate the issue described in the story by SEMANTIC match — find the symbol, function, pattern, or behavior the story describes. Do not require strict line-number match. If the symbol has been renamed but plays the same role (the issue is about the role, not the name), the story is still valid — do NOT mark stale on rename alone.
3. Decide:
   - If the issue still exists at any location → `still-present`. Provide the new line range where it now lives AND the file path.
   - If the issue is gone or has been transformed beyond recognition → `mark-stale` with a one-line reason.

## Output

Emit a single JSON object:

{
  "actions": [
    {
      "id": "ARCH-007",
      "action": "still-present",
      "new_lines": "12-78",
      "path": "src/forecastest/application/training/config.py"
    },
    {
      "id": "EXC-003",
      "action": "mark-stale",
      "reason": "the swallowed exception was replaced by typed handling; story description no longer matches"
    }
  ]
}

Do NOT modify story descriptions or recommendations — that's a manual re-evaluation step.
Do NOT touch stories with status: fixed/verified/wontfix.
```

## Output handling

Main agent applies actions to the review files:

- `still-present` action → locate the story by `id`; locate the cited file entry by matching `files[].path == path`; update that entry's `lines` to `new_lines`. Update the story's `last_verified_at` to `{commit: <HEAD SHA>, date: <YYYY-MM-DD>}`. (`path` is required even for single-file stories so the apply logic is uniform.)
- `mark-stale` action → locate the story by `id`; set `status: stale`; do not modify other fields. No `path` field is needed — the story is identified solely by `id`.

All applied actions are part of the same atomic commit as the code change.

## Why fresh context

Same rationale as correctness-pass: the implementation conversation knows what was done. A fresh subagent reads the current code and the story cold — it cannot conflate "what we did" with "what the story said."
