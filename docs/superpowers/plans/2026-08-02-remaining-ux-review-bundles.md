# Remaining UX Review Bundles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement and verify all 12 currently valid UX-review stories through six ordered topology bundles, leaving obsolete stories excluded and no open/stale valid stories unresolved.

**Architecture:** Work proceeds serially by bundle so shared certificate, credential, persistence, operator, deployment-doc, and dashboard contracts stabilize before dependent journeys are tackled. Each bundle has its own worktree/PR and focused plan; after merge, the UX rubric is refreshed before the next bundle starts. The parent plan coordinates order and gates; the focused plans contain exact file/test tasks.

**Tech Stack:** Python 3.14, FastAPI, Click, Pydantic, Redis, Uvicorn, Jinja2/HTMX, Docker Compose, Markdown, pytest, `uv`, Justfile gates.

## Global Constraints

- Execute in this exact priority/topology order: `01-cert-tls`, `02-token-auth`, `03-redis-schema`, `04-ops-cli`, `05-translategemma-docs`, `06-dashboard`.
- The scoped stories are exactly the 12 active stories in `docs/ux_review/summary.md`; do not reintroduce `DEPLOY-002`, `MAINT-004`, or `MAINT-019`.
- Stories remain independently traceable even when they share a worktree/PR; update `fixed_in` only after implementation and `verified_in`/`verified_by` only after independent journey evidence.
- Use one writer per worktree, TDD, typed interfaces, chained exceptions, `uv` dependency management, and no broad compatibility fallbacks.
- Every bundle passes `just validate`, `just ux-validate`, its per-surface gate, and fresh-context correctness/documentation-staleness reviews.
- After every merge, refresh the UX rubric against the new commit before opening the next bundle.
- Worktrees other than `master` live under `.worktrees/` and use the existing `ux-tackle/<theme>-bundle-<N>` naming convention.

## Ordered Bundle Plans

| Order | Focused plan | Stories | Branch/worktree |
|---:|---|---|---|
| 1 | `2026-08-02-bundle-01-cert-tls.md` | `MAINT-003`, `DEPLOY-008`, `MAINT-005` | `ux-tackle/maint-bundle-01`, `.worktrees/ux-tackle-maint-bundle-01` |
| 2 | `2026-08-02-bundle-02-token-auth.md` | `MAINT-007`, `MAINT-006`, `DEPLOY-012` | `ux-tackle/maint-bundle-02`, `.worktrees/ux-tackle-maint-bundle-02` |
| 3 | `2026-08-02-bundle-03-redis-schema.md` | `MAINT-018` | `ux-tackle/maint-bundle-03`, `.worktrees/ux-tackle-maint-bundle-03` |
| 4 | `2026-08-02-bundle-04-ops-cli.md` | `OPS-015`, `OPS-032` | `ux-tackle/ops-bundle-04`, `.worktrees/ux-tackle-ops-bundle-04` |
| 5 | `2026-08-02-bundle-05-translategemma-docs.md` | `DEPLOY-010`, `DEPLOY-013` | `ux-tackle/deploy-bundle-05`, `.worktrees/ux-tackle-deploy-bundle-05` |
| 6 | `2026-08-02-bundle-06-dashboard.md` | `OPS-033` | `ux-tackle/ops-bundle-06`, `.worktrees/ux-tackle-ops-bundle-06` |

All focused plans are relative to `docs/superpowers/plans/`.

## Task 1: Establish the clean execution baseline

**Files:**
- Read: `docs/superpowers/specs/2026-08-02-remaining-ux-review-bundles-design.md`
- Read: `docs/ux_review/SPEC.md`
- Read: `docs/ux_review/summary.md`
- Read: all six focused bundle plans

- [ ] Confirm `master` contains the UX refresh commit `8b9ad64` and the approved design commit `33e7a92`.
- [ ] Confirm the working tree is clean and `just ux-validate` passes.
- [ ] Confirm the 12 active stories have bundle metadata and the three obsolete stories have no bundle metadata.
- [ ] Record the baseline commands:

```bash
git status --short --branch
just ux-validate
```

- [ ] Do not start implementation until the baseline is clean.

## Task 2: Execute `01-cert-tls`

**Plan:** `docs/superpowers/plans/2026-08-02-bundle-01-cert-tls.md`

- [ ] Create `.worktrees/ux-tackle-maint-bundle-01` from `master` with branch `ux-tackle/maint-bundle-01`.
- [ ] Execute the focused plan in its priority/dependency order: safe certificate generation, certificate manager, lifecycle, admin/CLI surfaces, Uvicorn reload, integration evidence, and metadata.
- [ ] Run the focused tests after each seam, then run:

```bash
just validate
just first-run --step 2
just ux-validate
```

- [ ] Complete the independent status → replacement → reload → same-PID → worker-connectivity journey.
- [ ] Run fresh-context correctness and documentation-staleness reviews.
- [ ] Merge the bundle PR, remove its worktree/branch, and run `just ux-validate` on the merged `master`.
- [ ] Refresh the UX rubric and update active story citations before Task 3.

## Task 3: Execute `02-token-auth`

**Plan:** `docs/superpowers/plans/2026-08-02-bundle-02-token-auth.md`

- [ ] Create `.worktrees/ux-tackle-maint-bundle-02` from the refreshed `master` with branch `ux-tackle/maint-bundle-02`.
- [ ] Execute the source-of-truth contract, persisted store, orchestrator coordinator, worker provider, Compose/dashboard distribution, admin API, CLI, documentation, and first-run tasks.
- [ ] Keep explicit environment-token mode static and return remediation rather than pretending the orchestrator can mutate arbitrary worker environments.
- [ ] Run the focused tests after each seam, then run:

```bash
just validate
just first-run --step 1
just first-run --step 2
just first-run --step 3
just ux-validate
```

- [ ] Independently rotate a file-backed token, inspect secret-free audit history, verify every supported edge receives the new value, and dispatch a test job.
- [ ] Run fresh-context correctness and documentation-staleness reviews.
- [ ] Merge, remove the worktree/branch, run `just ux-validate` on `master`, refresh the UX rubric, and only then begin Task 4.

## Task 4: Execute `03-redis-schema`

**Plan:** `docs/superpowers/plans/2026-08-02-bundle-03-redis-schema.md`

- [ ] Create `.worktrees/ux-tackle-maint-bundle-03` from the refreshed `master` with branch `ux-tackle/maint-bundle-03`.
- [ ] Add failing legacy/version/corruption fixtures before changing `redis.py`.
- [ ] Implement schema version 1, version-0 normalization, strict direct reads, and resilient list visibility.
- [ ] Run:

```bash
uv run pytest --no-cov tests/shell/stores/test_redis_job_store.py tests/shell/test_job_store.py -q
just validate
just ux-validate
```

- [ ] Independently restart/read a representative old Redis record and confirm it remains visible alongside current records.
- [ ] Run fresh-context correctness and documentation-staleness reviews.
- [ ] Merge, remove the worktree/branch, refresh UX metadata, and run `just ux-validate` on `master`.

## Task 5: Execute `04-ops-cli`

**Plan:** `docs/superpowers/plans/2026-08-02-bundle-04-ops-cli.md`

- [ ] Create `.worktrees/ux-tackle-ops-bundle-04` from the refreshed `master` with branch `ux-tackle/ops-bundle-04`.
- [ ] Implement the public capability contract and tests before changing CLI rendering.
- [ ] Add missing-job remediation at the API error source, then render it in the generic stream error path without duplicating request IDs.
- [ ] Run:

```bash
uv run pytest --no-cov \
  tests/core/test_schemas.py \
  tests/shell/api/test_capabilities.py \
  tests/shell/api/test_jobs.py \
  tests/test_api_client.py \
  tests/shell/test_cli.py -q
just validate
just ux-validate
```

- [ ] Independently run `acheron capabilities --type tts` with a registered TTS worker and `acheron job tail missing-job`; record both outputs and exit status.
- [ ] Run fresh-context correctness and documentation-staleness reviews.
- [ ] Merge, remove the worktree/branch, refresh UX metadata, and run `just ux-validate` on `master`.

## Task 6: Execute `05-translategemma-docs`

**Plan:** `docs/superpowers/plans/2026-08-02-bundle-05-translategemma-docs.md`

- [ ] Create `.worktrees/ux-tackle-deploy-bundle-05` from the refreshed `master` with branch `ux-tackle/deploy-bundle-05`.
- [ ] Add failing first-run documentation-consistency assertions before rewriting either README.
- [ ] Correct offline 4b/12b prewarm and model-template instructions, then separate network-volume cache from container-disk storage without adding a numeric floor.
- [ ] Update the first-run workflow path filter for the worker README.
- [ ] Run:

```bash
just validate
just first-run --step 1
just first-run
just build-worker translategemma
docker compose --profile runpod-translation config --format json
just ux-validate
```

- [ ] Independently follow the documented model-switch and storage-allocation journeys and capture the deployer evidence.
- [ ] Run fresh-context correctness and documentation-staleness reviews.
- [ ] Merge, remove the worktree/branch, refresh UX metadata, and run `just ux-validate` on `master`.

## Task 7: Execute `06-dashboard`

**Plan:** `docs/superpowers/plans/2026-08-02-bundle-06-dashboard.md`

- [ ] Create `.worktrees/ux-tackle-ops-bundle-06` from the refreshed `master` with branch `ux-tackle/ops-bundle-06`.
- [ ] Add failing link, direct-route, fresh-client, and partial-preservation tests.
- [ ] Extract shared shell context, add `GET /jobs/{job_id}`, conditionally render selected detail in the shell, and retain `/partials/jobs/{job_id}`.
- [ ] Make the anchor durable while keeping HTMX’s partial request/history URL explicit.
- [ ] Run:

```bash
uv run pytest --no-cov dashboard/tests/test_dashboard.py dashboard/tests/test_job_detail.py -q
just validate
just first-run --step 2
just first-run --step 3
just ux-validate
```

- [ ] Independently confirm click → `/jobs/{id}` → reload → copied URL in a fresh/private session → Back/Forward behavior in Firefox or the approved equivalent.
- [ ] Run fresh-context correctness and documentation-staleness reviews.
- [ ] Merge, remove the worktree/branch, refresh UX metadata, and run `just ux-validate` on `master`.

## Task 8: Perform final rubric reconciliation

**Files:**
- Modify: `docs/ux_review/deploy.md`
- Modify: `docs/ux_review/maint.md`
- Modify: `docs/ux_review/ops.md`
- Modify: `docs/ux_review/summary.md`

- [ ] Run the current-head UX refresh after the final bundle merge; re-resolve every active story citation and re-exercise every journey.
- [ ] Confirm all 12 scoped stories are `verified`, all three obsolete resolutions remain documented, and no unrelated verified story was regressed.
- [ ] Confirm summary status counts show zero open/stale stories for the scoped themes and bundle labels remain ordered 01 through 06.
- [ ] Run the full final gate:

```bash
just validate
just ux-validate
git diff --check
git status --short --branch
```

- [ ] Run the final fresh-context correctness and documentation-staleness reviews.
- [ ] Commit only the final metadata refresh with `git commit -m "docs(ux-review): complete remaining remediation"`.

## Completion Gate

- [ ] Six bundle plans were executed serially in the approved order.
- [ ] Each bundle produced its own focused tests, user-journey evidence, review passes, and clean merge.
- [ ] All 12 valid stories are `verified`; `DEPLOY-002`, `MAINT-004`, and `MAINT-019` remain obsolete.
- [ ] `just validate`, all required first-run/build/manual gates, `just ux-validate`, and final review passes are green.
- [ ] The final worktree is clean and the branch/worktree cleanup contract is satisfied.
