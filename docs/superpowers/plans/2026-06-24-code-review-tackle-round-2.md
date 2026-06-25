---
topic: code-review-tackle-round-2
date: 2026-06-24
spec: 2026-06-24-code-review-tackle-round-2-design.md
branch: fix/code-review-tackle-2
worktree: .worktrees/code-review-tackle/
base_commit: b94cb75
parent_round: 2026-06-24-code-review-tackle-bundles-design.md
---

# Code Review Tackle — Round 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan bundle-by-bundle. Each sub-plan has checkbox (`- [ ]`) tasks. Tackle bundles in order B1 → B25.

**Goal:** Land the 123 remaining open code-review stories from `docs/code_review/` as 25 atomic bundles on `fix/code-review-tackle-2`, then FF-merge into `master`.

**Architecture:** Per-bundle execution. Each bundle = 1 or more commits (1 per story for behaviour changes; 1 per cross-cutting file for refactors). Per-story cycle: TDD (if behaviour change) → `just validate` gate → 2 fresh-context subagent passes (correctness + doc-staleness) → atomic commit with story code + review updates → `open` → `fixed` → `verified` (at end of bundle).

**Tech Stack:** Python 3.14, FastAPI, Pydantic v2, python-multipart, redis-py, httpx, runpod SDK, pytest, ruff, mypy. See `pyproject.toml` for pinned versions.

---

## Sub-plans

Each sub-plan is `docs/superpowers/plans/2026-06-24-code-review-tackle-round-2-b{NN}.md` and contains the per-story detail for one bundle.

| # | Bundle | Sub-plan | Stories | M-effort |
|---|---|---|---|---|
| B1 | Host path traversal (SEC-007) | [b01-host-path-traversal.md](2026-06-24-code-review-tackle-round-2-b01-host-path-traversal.md) | 1 | 1 |
| B2 | HttpWorker multipart cleanup | [b02-httpworker-multipart-cleanup.md](2026-06-24-code-review-tackle-round-2-b02-httpworker-multipart-cleanup.md) | 6 | 0 |
| B3 | HttpWorker multipart edge cases | [b03-httpworker-multipart-edge-cases.md](2026-06-24-code-review-tackle-round-2-b03-httpworker-multipart-edge-cases.md) | 6 | 0 |
| B4 | Settings sprawl A — loaders + env vars | [b04-settings-sprawl-a.md](2026-06-24-code-review-tackle-round-2-b04-settings-sprawl-a.md) | 6 | 0 |
| B5 | Settings sprawl B — model_id, output_mode | [b05-settings-sprawl-b.md](2026-06-24-code-review-tackle-round-2-b05-settings-sprawl-b.md) | 4 | 0 |
| B6 | Settings cleanup C — chars_per_token + factory | [b06-settings-cleanup-c.md](2026-06-24-code-review-tackle-round-2-b06-settings-cleanup-c.md) | 2 | 0 |
| B7 | Exception handling A — typed errors | [b07-exception-handling-a.md](2026-06-24-code-review-tackle-round-2-b07-exception-handling-a.md) | 6 | 0 |
| B8 | Exception handling B — observability | [b08-exception-handling-b.md](2026-06-24-code-review-tackle-round-2-b08-exception-handling-b.md) | 5 | 0 |
| B9 | RunPod forwarder security | [b09-runpod-forwarder-security.md](2026-06-24-code-review-tackle-round-2-b09-runpod-forwarder-security.md) | 5 | 0 |
| B10 | Health monitor & perf | [b10-health-monitor-perf.md](2026-06-24-code-review-tackle-round-2-b10-health-monitor-perf.md) | 7 | 3 |
| B11 | HttpWorker memory materialization | [b11-httpworker-memory-materialization.md](2026-06-24-code-review-tackle-round-2-b11-httpworker-memory-materialization.md) | 4 | 3 |
| B12 | Worker SDK consolidation | [b12-worker-sdk-consolidation.md](2026-06-24-code-review-tackle-round-2-b12-worker-sdk-consolidation.md) | 8 | 4 |
| B13 | Plan-time / chunking & OBS-011 | [b13-plan-time-chunking.md](2026-06-24-code-review-tackle-round-2-b13-plan-time-chunking.md) | 3 | 0 |
| B14 | Worker cleanup & TLS boilerplate | [b14-worker-cleanup-tls.md](2026-06-24-code-review-tackle-round-2-b14-worker-cleanup-tls.md) | 4 | 0 |
| B15 | TRANSLATEGEMMA handler refactor | [b15-translategemma-handler-refactor.md](2026-06-24-code-review-tackle-round-2-b15-translategemma-handler-refactor.md) | 3 | 3 |
| B16 | ARCH & type safety (low) | [b16-arch-type-safety-low.md](2026-06-24-code-review-tackle-round-2-b16-arch-type-safety-low.md) | 7 | 0 |
| B17 | Type safety — typed models & ignores | [b17-type-safety-typed-models.md](2026-06-24-code-review-tackle-round-2-b17-type-safety-typed-models.md) | 6 | 4 |
| B18 | Type safety — stringly-typed responses | [b18-type-safety-stringly-typed.md](2026-06-24-code-review-tackle-round-2-b18-type-safety-stringly-typed.md) | 3 | 0 |
| B19 | MAINT cleanup & Python 2 syntax | [b19-maint-cleanup-python2.md](2026-06-24-code-review-tackle-round-2-b19-maint-cleanup-python2.md) | 8 | 1 |
| B20 | Tests — handler + edge coverage | [b20-tests-handler-edge.md](2026-06-24-code-review-tackle-round-2-b20-tests-handler-edge.md) | 5 | 2 |
| B21 | Tests — orchestrator + step | [b21-tests-orchestrator-step.md](2026-06-24-code-review-tackle-round-2-b21-tests-orchestrator-step.md) | 6 | 2 |
| B22 | Tests — app + integration | [b22-tests-app-integration.md](2026-06-24-code-review-tackle-round-2-b22-tests-app-integration.md) | 7 | 0 |
| B23 | DOC + DX + PKG | [b23-doc-dx-pkg.md](2026-06-24-code-review-tackle-round-2-b23-doc-dx-pkg.md) | 6 | 1 |
| B24 | Auth + remaining SEC + stale bookkeeping | [b24-auth-sec-bookkeeping.md](2026-06-24-code-review-tackle-round-2-b24-auth-sec-bookkeeping.md) | 5 | 1 |
| B25 | Final cleanup + summary.md refresh | [b25-final-cleanup.md](2026-06-24-code-review-tackle-round-2-b25-final-cleanup.md) | 0 | 0 |

**Total: 25 bundles, 123 stories, 27 M-effort, 96 S-effort, ~85-105 commits.**

---

## Common workflow (used by every story)

### Pre-flight (once per bundle)

1. Switch to the worktree: `cd .worktrees/code-review-tackle` (worktree is shared with Round 1 but on new branch).
2. Confirm branch: `git branch --show-current` should be `fix/code-review-tackle-2`. If not, create: `git switch -c fix/code-review-tackle-2 master`.
3. Read the bundle's sub-plan file end-to-end.
4. For each story in the bundle, read the full story text in `docs/code_review/<theme>.md` (e.g. `correctness.md` for `CORR-*`, `architecture.md` for `ARCH-*`).

### Per-story cycle (TDD if behaviour change)

For each story in the bundle (in spec order):

#### Step 1: Write the failing test (only if behaviour changes)

- S-effort stories that are pure refactor (no observable behaviour change): skip TDD, go straight to Step 3.
- S-effort stories with a behaviour change (e.g. raise a new exception type): write a failing test first.
- All M-effort stories: write a failing test first.

Use the existing test file for the touched module (e.g. `tests/shell/test_orchestrator.py` for `src/acheron/shell/orchestrator.py`); create a new test file only if the touched module has no tests yet. Test name format: `test_<story_id_behaviour>`.

#### Step 2: Run test to verify it fails

```bash
uv run pytest tests/path/test_file.py::test_name -xvs
```

Expected: FAIL (function not found, or assertion fails for the right reason).

#### Step 3: Implement the minimal change

Edit the file(s) per the sub-plan. For S-effort, this is usually 1-10 lines. For M-effort, the sub-plan has the full code blocks.

#### Step 4: Run test to verify it passes

```bash
uv run pytest tests/path/test_file.py::test_name -xvs
```

Expected: PASS.

#### Step 5: Run the full verify gate

```bash
just validate
```

Expected: lint passes, type-check passes, all tests pass, coverage >= 80%.

If `just validate` fails on a pre-existing flaky test (e.g. `tests/integration/test_worker_integration.py`), note it in the commit message and continue (out of scope for Round 2 per spec section 6).

#### Step 6: Correctness subagent pass

Dispatch a fresh-context subagent with the prompt template at `references/subagent-prompts/correctness-pass.md` (from the code-review-tackle skill). Inline the story YAML + Issue + Why it matters + Recommendation + Verification from `docs/code_review/`, plus `git diff HEAD` of the uncommitted changes.

Acceptable verdicts:
- `addressed` → continue.
- `partial` → surface to user with justification; ask whether to continue or revert.
- `not-addressed` → revert the change, surface to user, abort the bundle.

#### Step 7: Doc-staleness subagent pass

Dispatch a fresh-context subagent with the prompt template at `references/subagent-prompts/doc-staleness-pass.md`. Inline the list of touched files. The subagent returns a JSON list of `still-present` / `mark-stale` actions for OTHER stories citing the touched files (not the story being fixed).

Apply the actions:
- `still-present` → update the story's `last_verified_at` to `{commit: pending, date: 2026-06-24}` and update line ranges if the file moved.
- `mark-stale` → set the story's `status: stale` (only for stories with `status: open | in-progress`).
- Skip the just-tackled story itself (it stays `open` until the bundle completes).

#### Step 8: Atomic commit

Stage both the code change AND the review updates:

```bash
git add src/ tests/ docs/code_review/
git diff --cached --stat  # verify the right files
git commit -m "fix(<STORY-ID>): <1-line description>

<2-3 sentence body explaining what changed and why. Use bullet points
if the change touches multiple files.>"
```

For multi-story cross-cutting fixes that share a code path, use a single commit with multiple IDs: `fix(STORY-A, STORY-B): <description>`. The sub-plan marks these as "single commit" entries.

Commit message style (from AGENTS.md):
- Conventional Commits format.
- Optional scope in parens (recommended).
- Concise: title 1 line, body 2-3 lines max.
- Bullet points allowed for multi-file changes.

#### Step 9: Bump story status

For each story fixed in the commit:
- In `docs/code_review/<theme>.md`, change `status: open` to `status: fixed`.
- Update `last_verified_at: {commit: <commit-sha>, date: 2026-06-24}`.
- Update `fixed_in: [<commit-sha>]`.
- Update `lines:` ranges to the new post-fix locations.
- (The `verified` status is bumped at end of bundle, not per-commit.)

If the commit also touches review updates for OTHER stories (step 7), include those in the same commit — don't make a separate "review updates" commit per the code-review-tackle skill.

### End-of-bundle cycle

After all stories in the bundle are committed:

1. Bump all fixed stories to `status: verified` with a `docs(code-review): record <BUNDLE-NAME> verification` commit. Include any cross-cutting review updates (e.g. line-range shifts for stories not in this bundle).
2. Run the final gate: `just validate`.
3. Report bundle summary to the user: story count, commit count, M-effort designs that needed iteration, any partial verdicts.
4. User reviews and decides whether to push, PR, or continue to next bundle.

### Per-round cycle

After all 25 bundles land on `fix/code-review-tackle-2`:

1. Switch to main worktree: `cd /home/julia/devel/acheron` (NOT the code-review-tackle worktree).
2. `git merge --ff-only fix/code-review-tackle-2` (must be fast-forward; the worktree was rebased on each commit).
3. `git push` to both remotes (codeberg + github).
4. Update `docs/superpowers/specs/2026-06-24-code-review-tackle-round-2-design.md` with the final tally (no code change to the spec, just a comment).
5. (Out of scope) Run `code-review-update` for Round 3.

---

## Conventions

### Commit message

- Use Conventional Commits: `fix(<STORY-ID>): <title>` or `docs(code-review): <purpose>`.
- Scope in parens when there are multiple IDs: `fix(SEC-014, SEC-016): <description>`.
- 1-line title; 2-3 line body.
- For multi-file changes, use bullet points in the body.
- No "🤖 generated by" footer; no "Co-authored-by" trailers.
- Reference related story IDs in the body if helpful (e.g. `Refs CORR-015 (correctness angle on the same code).`).

### Branch and worktree

- Worktree: `/home/julia/devel/acheron/.worktrees/code-review-tackle/` (existing, reused from Round 1).
- Branch: `fix/code-review-tackle-2` (new, rebased onto `master @ b94cb75`).
- The Round 1 worktree may still hold the Round 1 branch `fix/code-review-tackle` for reference; check it out separately if needed.

### When to surface to user

Surface to user (don't auto-decide) when:
- Correctness verdict is `partial` or `not-addressed`.
- Doc-staleness subagent returns `mark-stale` for a story that wasn't expected to go stale.
- `just validate` fails on a test that wasn't pre-existing-flaky.
- M-effort design needs >1 commit (split decision).
- A story's YAML metadata is structurally broken (e.g. `last_verified_at` missing required fields).

### Out of scope for this round

- Stale-bookkeeping items (SEC-011, OBS-007, OBS-009) — handled in B24 as 1-line YAML updates.
- `code-review-update` rerun — deferred to Round 3.
- Dashboard stories — none open; settings knobs added in Round 2 may need dashboard updates in Round 3.
- Pre-existing test flakiness in `tests/integration/test_worker_integration.py` and `tests/integration/test_tls.py`.
- New layers / new features.

---

## Reference: subagent prompt templates

Located at `.agentic/skills/code-review-tackle/references/subagent-prompts/`:

- `correctness-pass.md` — verifies a story was addressed by a commit.
- `doc-staleness-pass.md` — finds other stories that may have shifted due to a touched file.

Read these once before the first bundle to internalise the format. Each per-story subagent invocation should:

1. Inline the story YAML + Issue + Why it matters + Recommendation + Verification from `docs/code_review/<theme>.md`.
2. Inline `git show <sha>` (or `git diff HEAD` for uncommitted changes) of the touched files.
3. Expect a JSON verdict or actions object as output.

For uncommitted changes, use `git diff HEAD -- <cited-paths>` to scope the diff.

---

## Risk register (recap from spec)

- **B19** is the largest bundle (8 stories). Tackle mechanical fixes (MAINT-005, -008, -009, -012, CORR-031) first; leave MAINT-002 (M-effort redis JSON), MAINT-006 (token block), MAINT-007 (HTTP envelope) for last.
- **B10** has 3 M-effort stories (OBS-001, TEST-007, CORR-012) that interact. Tackle OBS-001 (drain) first, then TEST-007 + CORR-012 (transitions), then PERF-* (concurrency).
- **B11** has 3 M-effort stories (CORR-017, -018, -019) needing a streaming protocol. Tackle CORR-018 first (request side is the most constrained), then CORR-017, then CORR-019.
- **SEC-007** (B1) is the only HIGH-severity; it's also M-effort. Take extra care with the path-resolution allowlist.
- M-effort designs may surface new work mid-implementation. If a design needs >1 commit, split into multiple commits and add a follow-up review entry — do not auto-expand the bundle.

---

## Self-review (after writing all 25 sub-plans)

After the 25 sub-plans are written, run the spec self-review checklist:

1. **Spec coverage:** every story in the spec has a corresponding sub-plan section. List any gaps.
2. **Placeholder scan:** no "TBD", "TODO", "implement later" in any sub-plan. Every M-effort sub-plan has the full TDD code.
3. **Type consistency:** types, method names, file paths used across sub-plans match the spec.

If issues are found, fix inline. No re-review needed.
