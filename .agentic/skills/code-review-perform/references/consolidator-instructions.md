# Consolidator Instructions

The consolidator is the main agent's post-subagent pass. It receives 6 bundle subagent outputs (one JSON object each) and produces the final `docs/code_review/` files.

## Inputs

- 6 bundle outputs of shape `{bundle, findings, bundle_notes}` (see bundle prompts).
- Existing `docs/code_review/` content (empty for `perform`; populated for `update`).
- Current branch and HEAD SHA.
- Last `last_updated_commit` from existing files (empty for `perform`).

## Algorithm

### Step 1: Flatten and tag

Collect all findings into a single list, preserving the bundle that produced each. Validate JSON shape; reject malformed entries (log to stderr but do not abort).

### Step 2: Dedup via cross-reference

For each pair of findings (i, j) where i.bundle != j.bundle:

- Apply the file-overlap tiebreaker first: if the shared `files[].path` set covers >50% of either finding's file list, this is sufficient evidence — link them. If file overlap is partial (1 shared file, each has others), then ALSO require semantic similarity (similar wording in `issue` or shared root cause in `recommendation`) before linking. This split prevents "judgment call" from producing divergent `related` graphs across runs.
- When the criterion is met:
  - Pick the primary: use the bundle whose lens most directly fits the root cause. Heuristic: SEC > CORR > ML > MATH > ARCH > CFG > MAINT > EXC > TYPE > REPRO > DATA > PERF > OBS > TEST > DX > PKG > DOC.
  - Add `j.prefix-N` to `i.related`. Add `i.prefix-N` to `j.related`.
  - DO NOT delete j. Both stories are kept; cross-references make the relationship visible.

### Step 3: Assign IDs

For each prefix (CORR, ML, MATH, ARCH, ...):

- **`perform` mode**: assign `<PREFIX>-001`, `-002`, ... in severity desc, then file-path asc order.
- **`update` mode**: read existing `<PREFIX>-NNN` IDs from current files. New findings get `<PREFIX>-(max+1)`, `<PREFIX>-(max+2)`, etc.

### Step 4: Compute grades

For each of the 17 prefixes:

- Count stories with `status: open | in-progress | stale` of each severity.
- Apply `grading-rubric.md` thresholds; take the worst matching grade.

### Step 5: Write per-bundle files

For each of the 6 bundles, write `docs/code_review/<bundle-slug>.md`:

- `correctness.md`: prefixes CORR, ML, MATH (one subsection per prefix)
- `architecture.md`: prefixes ARCH, CFG
- `code-quality.md`: prefixes MAINT, EXC, TYPE
- `verification.md`: prefixes TEST, REPRO, DATA
- `operations.md`: prefixes PERF, OBS, SEC
- `surface.md`: prefixes DX, PKG, DOC

File structure:

```markdown
---
branch: <branch>
initial_review_commit: <sha-at-creation>   # frozen; preserve from existing if updating
last_updated_commit: <head-sha>
last_staleness_scan:
  commit: <head-sha>
  date: <today>
---

# <Bundle Name>

## <THEME-PREFIX> — <Theme Name>

**Grade:** <A-F>

<one-paragraph narrative explaining the grade>

### <PREFIX>-NNN — <Title>

[YAML metadata + prose per `story-template.md`]

### <PREFIX>-NNN — ...
```

Order stories within a theme by `severity desc, then id asc`.

### Step 6: Write `summary.md`

```markdown
---
branch: <branch>
initial_review_commit: <sha>
last_updated_commit: <sha>
last_staleness_scan:
  commit: <sha>
  date: <today>
---

# Code Review Summary

## Per-theme grades

| Theme | Grade | Stories (open/in-progress/stale) |
|---|---|---|
| CORR | A | 0 critical, 1 high, 2 medium |
| ML   | C | 1 critical, 0 high, 0 medium |
... (one row per prefix; 17 rows total across 6 bundles)

## Top concerns

Stories sorted by severity desc, then ID asc, top 10 (or all if fewer than 10). Each entry cites the bundle file the story lives in (e.g., `correctness.md` for ML, `architecture.md` for ARCH).

1. ML-001 — <title> [critical] — `correctness.md`
2. ARCH-002 — <title> [high] — `architecture.md`
3. ...

## Quick wins

Stories with `effort: S` AND `severity ∈ {medium, high, critical}` AND `status ∈ {open, in-progress, stale}`:

1. EXC-003 — <title> [medium, S effort] — `code-quality.md`
2. ...

## Story counts

| Status | Count |
|---|---|
| open | N |
| in-progress | N |
| fixed | N |
| verified | N |
| stale | N |
| wontfix | N |

## Last orientation snapshot

[Inline the orientation prelude brief produced by the main agent, ≤200 lines.]
```

### Step 7: Stage and commit

For `perform`:

```bash
git add docs/code_review/
git commit -m "docs(code-review): initial review at <head-sha>"
```

For `update`:

```bash
git add docs/code_review/
git commit -m "docs(code-review): refresh at <head-sha>"
```

## SHA placeholder resolution (update mode only)

On `perform` mode runs, all `fixed_in` start empty (no work has been done yet). On `update` mode runs, scan existing files for `fixed_in: ["pending"]` (or pending entries within a list). For each:

- Walk `git log --grep="(<ID>)" --oneline` to find commits whose Conventional Commits scope includes the story ID.
- Replace `pending` with the SHA(s) found.
- If no match found, leave `pending` and emit a warning ("Could not resolve SHA for <ID>; commit message scope may be wrong").

## Update-mode immutability

Do NOT modify stories with `status: fixed | verified | wontfix`. These are append-only history. New findings get new IDs even if they describe a regression.

Do NOT overwrite `initial_review_commit` on update runs. Copy it verbatim from the existing file into the new file. Only `last_updated_commit` and `last_staleness_scan` advance.

## Failure modes

- A bundle subagent returns malformed JSON: skip its findings, log warning, continue. Never abort the whole consolidation.
- Existing file YAML frontmatter is malformed: surface error and abort (don't silently corrupt).
- Branch protection: if HEAD is on `main`/`master`/`develop`, abort with explicit error.
