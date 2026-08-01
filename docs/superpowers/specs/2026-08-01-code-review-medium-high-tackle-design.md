# Code Review Medium/High Tackle Design

## Goal

Address all 21 currently open high- or medium-severity code-review stories in topology-ordered bundles, with a worker/reviewer loop for each bundle and one atomic commit per story.

## Scope

Included stories:

- High: `ARCH-027`, `DX-008`
- Medium: `ARCH-028`, `ARCH-029`, `MAINT-024`, `MAINT-025`, `EXC-006`, `EXC-007`, `CORR-045`, `CORR-046`, `CORR-047`, `OBS-016`, `PERF-012`, `PERF-013`, `PERF-014`, `PERF-015`, `DOC-014`, `DX-009`, `TEST-031`, `TEST-032`, `TEST-033`

Stories whose status is stale, fixed, verified, or wontfix are excluded. Each bundle receives a pre-flight staleness check against the current code; materially obsolete stories are marked stale in a documentation-only commit and removed from implementation.

## Bundle topology and order

1. **Orchestration and cache boundaries:** `ARCH-027`, `ARCH-028`, `PERF-014`
2. **Job-event lifecycle:** `MAINT-024`, `CORR-045`, `PERF-012`, `TEST-033`
3. **Jobs route structure and warning failures:** `MAINT-025`, `EXC-006`
4. **Public API schemas and client boundaries:** `ARCH-029`, `CORR-046`
5. **Retention and administrative observability:** `EXC-007`, `OBS-016`
6. **Dashboard URL and polling surfaces:** `DX-008`, `PERF-013`
7. **Worker SDK resource handling and pricing:** `CORR-047`, `PERF-015`
8. **Output and audio validation coverage:** `TEST-031`, `TEST-032`
9. **Developer documentation and validation tooling:** `DOC-014`, `DX-009`

The order starts with shared orchestration state, then event and route behavior, public boundaries, operational surfaces, user-facing dashboard behavior, worker concerns, focused coverage, and finally repository documentation/tooling.

## Execution model

Work occurs only in `.worktrees/code-review-medium-high` on branch `fix/code-review-medium-high`, created from the current local `master`. The parent session remains the orchestrator and final decision-maker. Only one mutation-capable worker writes the active worktree at a time.

For each bundle:

1. Dispatch one worker with the complete story text, cited files, recommendations, scope limits, and focused validation targets.
2. Require test-first changes for behavior fixes and tests required by the story.
3. Run formatting and focused tests after the worker handoff.
4. Dispatch fresh read-only reviewers in parallel for correctness/regressions, tests/validation, and maintainability; add surface-specific angles for API, security, performance, or dashboard work.
5. Synthesize findings in the parent. Apply only concrete fixes within the approved story scope through one fix worker.
6. Repeat review and fix passes for up to five rounds per bundle; stop earlier when no blockers or worthwhile fixes remain. Escalate unapproved architecture or product decisions to the user.
7. Run the formal correctness and documentation-staleness passes required by `code-review-tackle`.
8. Update matching review entries, then create one atomic `fix(<STORY-ID>): <summary>` commit per addressed story. Dependent stories are committed in dependency order.

If `master` advances while a bundle is complete and the worktree is clean, rebase the branch onto `master` before starting the next bundle. Never modify `master` directly and do not push or open a PR unless separately requested.

## Validation contract

The repository's native gate is authoritative:

```bash
just validate
```

This runs Ruff formatting/checks, import-linter, mypy, basedpyright, and the full pytest suite. Workers also run the smallest relevant focused tests before the full gate. The generic Poetry/dbt command from the shared tackle reference is not used because Poetry and dbt are unavailable in this environment and the repository has no dbt project or models.

A bundle cannot advance after a failed required gate. The baseline on the new branch is green: `just validate` passes with 1,695 tests passed and 9 skipped.

## Non-goals

- Do not tackle low-severity or closed review stories.
- Do not redesign unrelated modules discovered during implementation.
- Do not bypass tests, lint, typing, import-boundary checks, or review passes.
- Do not combine unrelated story commits merely because one worker handled a bundle.

## Completion criteria

The run is complete when every remaining selected story is verified in its review document, every story commit passes the native validation gate, all review-loop findings are dispositioned, the final diff has been inspected by the parent, and any deferred findings or stale-story exclusions are reported with their reasons.
