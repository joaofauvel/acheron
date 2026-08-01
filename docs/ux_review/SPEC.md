---
program: ux-review
status: active
version: 2
last_updated_date: 2026-07-24
prior_versions:
  - version: 1
    date: 2026-07-24
    status: superseded
    notes: Initial draft; superseded by v2 after the 5-agent adversarial review.
related: docs/code_review/
---

# Acheron UX Review Program — Master Spec

The spec that ties together the UX review rubric, the deployment simulation, and the first-run journey test. Read this first; everything else in `docs/ux_review/` references back to it.

## 1. Purpose

UX review is a parallel program to code review (`docs/code_review/`). Code review asks "is the code correct, fast, secure, type-safe?" UX review asks "does an operator / deployer / on-call maintainer actually use this without pain?"

Acheron's primary supported deployment target is **RunPod Serverless**. This program audits the RunPod-flavored experience. Other targets (e.g. local Docker, other GPU clouds) are out of scope unless explicitly added.

The two programs share infrastructure (worktree flow, story YAML format, skill trio) but live in different files. A commit can resolve both a code-review story and a UX story. The relationship is one-way reference via the `related:` field, not co-ownership.

### 1.1 Cross-program boundary

When a single commit closes both a code-review story *and* a UX story, both move to `fixed` and `verified` independently. The code-review story is closed first (it carries the commit-anchored `fixed_in` and `verified_in`); the UX story is closed second (its `Verification` block is a user-journey check the code-review `Verification` block does not assert). A UX story is **never** the system of record for an in-flight-task, type-safety, registration-token, or other code-review-theme defect — those are the code-review rubric's job, and a UX cross-ref via `related:` is the only correct shape. A PR that closes a `related` code-review story must include `Closes-CodeReview: <id>` in the commit message trailer so the next `code-review-update` scan finds it.

This program exists because the RunPod main use case (deploy → operate → maintain) has friction the code-review rubric does not catch:

- The README is materially wrong in several places; the deployer doesn't know.
- The dashboard renders only three read-only tables; the operator falls back to `docker logs`.
- The cost basis (`MEASURED` / `CACHED` / `UNKNOWN` / `STATIC`) is rendered but never explained.
- A `kill -9` on the orchestrator leaves jobs in `RUNNING` forever; no admin endpoint reaps them.

The program produces:

1. A **rubric** for cataloguing UX pain (Phase 1-2).
2. A **runtime simulation** that exercises RunPod control-plane behavior (Phase 3a).
3. A **first-run journey test** that exercises the deployment path (Phase 3b).
4. **Tackle bundles** that ship user-visible fixes (Phase 4+).

## 2. The three themes

Every UX story lives in exactly one of three themes. Cross-cutting concerns (security, cost) surface as stories inside the relevant theme, not as their own themes; the `journey_stage` YAML field (§3.1) provides an orthogonal axis for cross-cutting stories.

### DEPLOY — first-run setup, runpodctl flow, env wiring, HF cache pre-warm, cert mint, image build/publish

What the deployer sees on day 0. Stories typically land in `docker-compose.yml`, the worker READMEs, the Justfile, `Dockerfile.runpod`, `scripts/generate_dev_certs.py`.

**Calibration target**: a developer who has used Docker but never used RunPod, given 1 day, should succeed without help.

### OPS — CLI ergonomics, dashboard, job submission, observability, cost transparency, error messages

What the operator sees day-to-day. Stories typically land in `src/acheron/cli.py`, `dashboard/`, `src/acheron/api_client.py`, `src/acheron/shell/api/routes/`.

**Calibration target**: an operator should be able to submit, monitor, debug, and recover a job without `docker logs`.

### MAINT — rotation, cleanup, log aggregation, rollback, version pin, data dir hygiene, cert renewal, recovery endpoints

What the on-call maintainer sees at 2am. Stories typically land in `src/acheron/shell/orchestrator.py`, `src/acheron/worker_sdk/pricing.py`, `src/acheron/worker_sdk/_runpod_client.py`, `src/acheron/shell/health.py`, the cert generator.

**Calibration target**: an on-call engineer should be able to recover from a 2am page without paging someone else.

### 2.1 Theme tie-breaker

When a story's underlying defect spans themes (e.g., a `docker-compose.yml` issue that affects DEPLOY, OPS, and MAINT personas), choose the theme by the **persona who first notices the failure** in the operator's typical workflow. The `journey_stage` field (§3.1) carries the temporal axis: `t0` (clone) / `t1` (use) / `t2` (recover) / `cross_cutting` (all three). If a single story produces both an OPS and a MAINT narrative (e.g., "operator cannot cancel" is OPS, "on-call cannot force-reap" is MAINT), file two stories linked by `related:`.

## 3. Story schema

Each story is a single markdown file section with YAML frontmatter + four prose blocks.

### 3.1 YAML frontmatter

```yaml
---
id: DEPLOY-001                     # theme prefix + 3-digit number
title: <one-line, imperative or descriptive>
status: open | in-progress | fixed | verified | partial | stale | obsolete | broken-yaml | wontfix
severity: high | medium | low       # friction-blocks / friction-costs / paper-cut
effort: S | M | L                   # <=1d / <=1w / >1w
discovered_via: [<channel>, ...]   # ordered list, strongest evidence first
user_facing_surface: cli | dashboard | compose | worker-image | runpod-api | certs | quickstart | internal
silent: true | false                # does the failure announce itself? (silent=true is worse)
journey_stage: t0 | t1 | t2 | cross_cutting   # clone / use / recover / all
user_journey: "<the journey this breaks, in present tense, with starting and ending state>"
files:
  - path: <path>
    lines: <N-M>                    # inclusive range; "89-112"; single line "89"
related: [DEPLOY-002, OPS-003, CORR-001]   # may include code-review IDs
bundle: a                          # optional; groups stories into one worktree
fixed_in: [<sha>, ...]             # initially empty; populated by ux-review-update
verified_in: [<sha>, ...]          # initially empty; populated by ux-review-update
last_verified_at: {commit: <sha>, date: <iso-date>}  # initially empty
verified_by: <handle>              # initially empty; MUST differ from pr_author
incident_ref: <link>               # required when discovered_via: [on-call, ...]
feedback_ref: <link>               # required when discovered_via: [user-feedback, ...]
wontfix_reason: out-of-scope | wontfix-product | wontfix-cost | wontfix-ux-traded-off | duplicate  # required when status: wontfix
---
```

Field semantics:

- **`discovered_via` is an ordered list**. The first entry is the *trigger* (the channel that surfaced the finding); subsequent entries are *confirmations*. Strength ordering: `user-feedback > on-call > first-run ≈ simulation > audit > code-review`. When a story surfaces in two channels simultaneously and the filer cannot determine the trigger, file it under the channel with the strongest evidence (the channel that *nailed down* the finding, not the one that merely *mentioned* it). v1 of the spec uses this list semantically; downstream tooling (Phase 2b's `ux-review-update`) uses it for the verification-gate.
- **`silent: true` is the most operationally important flag**. It marks stories where the system fails without giving the operator a usable signal (e.g. "your token is wrong" or "your HF cache is stale"). The §10 anti-patterns force this flag to default high; the downgrade is a normal PR-time edit during review.
- **`user_journey`** must name both a starting state and an ending state. "Operator submits a job" is not a journey; "operator submits a job, sees BOOTING with a 30s countdown, then sees HEALTHY" is.
- **`journey_stage`** is orthogonal to the theme. It is what `user_facing_surface` could not be: the temporal axis. A `DEPLOY` story with `journey_stage: t0` is "during first clone"; a `DEPLOY` story with `journey_stage: t1` is "during routine use, the deployer hits a stale deploy artifact." The `cross_cutting` value is the runtime cross-cutting-stories signal; it pairs with any theme.
- **`user_facing_surface: quickstart`** replaces the prior `first-run` value to disambiguate from `discovered_via: first-run` and the §8 first-run journey test. The three uses of the word now mean three distinct things.
- **`user_facing_surface: internal`** is reserved for surfaces below the user-facing API (e.g. internal cache, internal transport). Use only when no other enum value applies and the operator still observes the effect indirectly.
- **`related:`** accepts any story ID, present or future, in any program; IDs are not validated. Self-reference is allowed but discouraged. A reference to a `wontfix` story is allowed; the new story must justify why it is not also `wontfix`.
- **`fixed_in` and `verified_in`** start empty. `ux-review-update` resolves `"pending"` placeholders to commit SHAs whose Conventional Commits scope includes the story ID.
- **`last_verified_at.commit`** is the harness's last-green SHA (for `discovered_via: simulation | first-run`) or the manual verification commit (for human-only paths). Tracked metadata may use the literal `CURRENT_HEAD`; the verifier resolves it only when it matches the repository's actual checked-out HEAD, never an arbitrary supplied SHA.
- **`verified_by`** must be someone other than the PR author. A harness artifact is acceptable as a "second pair of eyes" when the PR author is also the harness author — the harness itself is the second witness.
- **`bundle`** is optional and groups stories into one worktree. Mirror the code-review `tackle/round-N` pattern. Use when stories share a `user_facing_surface` and non-conflicting `files` ranges.

### 3.2 Prose template

Each story has four blocks. Concise, evidence-backed, file:line references throughout.

```
**Issue.** What the system does today. Cite file:line.

**Why it matters.** What the operator/deployer/maintainer experiences. Quantify when possible (e.g. "5 of 14 on-call failure modes require this").

**Recommendation.** What to change. Avoid proposing implementation; describe the desired behavior.

**Verification.** How to know the fix worked. For UX stories, this is a user-journey check, not an assertion-style test. A `just test` line belongs in the code-review story; the UX story's Verification is what a human (or a harness) does to confirm the fix worked. If the fix changes user-facing surface behavior, the PR MUST update the corresponding doc (README, `--help`, dashboard help panel) in the same commit.
```

### 3.3 Story ID lifecycle

| Status | Meaning |
|---|---|
| `open` | Filed, not yet tackled. Default new-story status. |
| `in-progress` | Active work in a worktree. One story (or one bundle), one worktree, one PR. |
| `fixed` | PR merged; awaiting verification against the user_journey. |
| `partial` | PR merged but the user journey is only partially satisfiable. Re-open to `in-progress` for the remaining fix. |
| `verified` | User journey confirmed by a passing harness artifact OR by an explicit `verified_by` whose handle differs from the PR author. `last_verified_at` is populated. Terminal. |
| `stale` | Concern unchanged but the code changed; the file:line range still resolves. Re-resolve via `ux-review-update`. |
| `obsolete` | Concern no longer valid (code or spec changed). Close without action. Terminal. |
| `broken-yaml` | Frontmatter parse failed. The `ux-review-update` skill surfaces the malformed file. The story is not lost — the file is the source of truth. |
| `wontfix` | Conscious decision not to fix. `wontfix_reason` is required. Terminal. |

`fixed` is not done. `verified` is done. The two-step is intentional: fixes often miss the actual user journey.

Regressions are re-filed as a new story ID with `related: [<old-id>]`; the old story moves to `stale` or `obsolete` depending on whether the new commit fully restores behavior. There is no `regression` status.

## 4. Severity & effort

### 4.1 Severity

| Severity | Definition | Example |
|---|---|---|
| `high` | Blocks the happy path. The operator cannot complete the workflow without manual surgery. | "No `acheron job cancel` — must `docker compose restart orchestrator` to abort." |
| `medium` | Measurable friction on a common flow. The operator can complete the workflow but it costs time or attention. | "Cost basis badge is rendered without explanation; operator doesn't know what `CACHED` means." |
| `low` | Paper cut. Annoying but recoverable. | "Dashboard's progress bar shows `5/20` without per-chapter breakdown." |

### 4.2 Effort

| Effort | Definition |
|---|---|
| `S` | <= 1 day. Trivial fix; can be batched. |
| `M` | <= 1 week. Needs a design decision or touches multiple files. |
| `L` | > 1 week. Cross-cutting refactor or new subsystem. |

A `high` severity with `S` effort is a **quick win** — the highest-ROI target.

## 5. Discovery channels

The rubric tracks *how* a story was found, not just *what* it is. The `discovered_via` field is an ordered list (§3.1); the first entry is the trigger, subsequent entries are confirmations.

| Channel | Strength | Weakness | Use for |
|---|---|---|---|
| `code-review` | Reproducible; covers all source | Misses runtime behavior | Structural gaps (missing CLI command, absent route) |
| `simulation` (Phase 3a) | Reproducible; tests live behavior | Doesn't cover deployment | Runtime control-plane behavior (pricing, GPU switch, cold start) |
| `first-run` (Phase 3b) | Reproducible; tests the README path | Doesn't cover real RunPod | Deployment friction (certs, env wiring, token orchestration) |
| `on-call` | Covers real failure modes | Not reproducible without incident | Recovery gaps (drain, reap, mark-failed) |
| `audit` | Systematic; covers cost + security | Reader's bias | Security footguns, cost surprises |
| `user-feedback` | Ground truth | Hardest to weight, may not generalize | Anything a human reported |

## 6. The phases plan

| Phase | Sub-phase | Deliverable | Days | Behavior change? | Depends on |
|---|---|---|---|---|---|
| **1** | 1. Rubric + 10 stories | `docs/ux_review/{summary,deploy,ops,maint}.md` filed with the 10 UX-original stories from the swarm's synthesis | ~0.25 | no | — |
| **2** | 2a. Remaining 50+ stories | All findings from the swarm's 5 adversarial agents filed, themed, ranked, with `related:` to code-review counterparts where applicable | ~0.25 | no | 1 |
| | 2b. `ux-review-*` skills + `just ux-validate` | Mirror `code-review-{perform,update,tackle}` for UX; add `ux-validate` and `ux-verify` Justfile targets | ~1 | no (CI-only) | 1 |
| **3** | 3a. Runtime simulation | Extend `mock_runpod.py`, `compose/sim.yml`, 2 Justfile targets, 3 scenarios with JSON-oracle assertions; add `just sim-run` | ~5-7 | no (new harness only) | 1, 2a |
| | 3b. First-run journey test | Fresh-checkout smoke test in CI: README-verbatim deploy + assert success criteria; add `just first-run` | ~2-3 | no (CI-only) | 1 |
| **4** | First tackle bundle | 5 stories (M-effort or less, sim-validated where possible) | ~5 | yes (user-visible) | 3a, 3b |

Each sub-phase ends in a mergeable PR. Phases 1-3 are no-behavior-change (process + harness). Phase 4 is the first behavior change.

**PR contract**: a sub-phase PR is mergeable iff (a) `just validate` passes, (b) for Phase 2b+, `just ux-validate` passes, (c) for Phase 3a, the relevant `just sim-run <scenario>` passes, (d) for Phase 3b, the relevant `just first-run --step <N>` passes.

Dependency graph: 1 -> 2a -> 2b -> 3a; 3b is independent of 3a; 4 depends on 3a + 3b.

## 7. The simulation (Phase 3a)

### 7.1 Goal

> The simulation exists to answer one specific class of question the existing `tts-runpod-stub` cannot: **what does the operator experience when the RunPod control plane behaves badly?** Concretely: (a) does the worker's pricing source recover correctly when RunPod's GraphQL fails, (b) does the dashboard surface the new GPU/price after an endpoint patch, (c) does the operator CLI surface a clear, structured error after a 5xx, and (d) does the health provider correctly report BOOTING for a cold endpoint. Everything else — wiring, registration, end-to-end job execution — is already covered by `tests/integration/test_job_lifecycle.py` and the live `tts-runpod-stub`. The simulation must not duplicate those; it must isolate the RunPod-specific failure modes in `src/acheron/worker_sdk/pricing.py`, `_runpod_client.py`, and `shell/health_providers.py:50-72`.

### 7.2 Success criteria

1. Pricing-refresh recovery has a regression test that doesn't require internet.
2. GPU-switch round-trip is observable end-to-end (asserted on JSON, not on logs).
3. Failure modes are 1:1 with scenarios (`sim/scenarios/INDEX.md`).
4. The simulation runs without operator eyes (zero grep/eyeball steps in the success path).
5. Build cost is bounded (<=150 LoC mock + <=200 LoC harness + 3 scenarios at <=80 LoC each).

### 7.3 CI signal

The simulation's CI signal is `just sim-run <scenario>`. A story with `discovered_via: [simulation, ...]` is mechanically verified when the relevant scenario:

- Exists at `sim/scenarios/<name>.py`.
- Contains `STORY_REF: <STORY-ID>` in its docstring.
- Was last green on a commit post-merge of `fixed_in[0]`.
- References the story's `user_journey` text (drift detection).

`ux-review-update` automates the `fixed -> verified` transition when all four hold.

### 7.4 What the sim CAN tell us

- Whether the worker's pricing refresh survives a GraphQL outage.
- Whether the dashboard refresh tick picks up a GPU switch.
- Whether the operator CLI surfaces a typed error vs a traceback on RunPod 5xx.
- Whether `RunPodHealthProvider.check_status` correctly returns BOOTING then HEALTHY on cold start.
- Whether the `OBS-001` drain fix holds under real SIGTERM with background persists.

### 7.5 What the sim CANNOT tell us (scope-bounding)

- Whether real RunPod SIGKILL bills the user money (mock has its own clock/billing semantics).
- Whether the operator's UX is good (sims assert on JSON, not on humans).
- Whether three worker types coexist correctly in prod (sim runs locally).
- Whether the data-dir cleanup policy is correct (product question, not sim question).
- Whether the `runpod` Python SDK is future-compatible (vendor risk is invisible to the sim).

### 7.6 Scope

Extend the existing `stubs/_sdk_base/mock_runpod.py` (do not create a new file). Add:

- `POST /graphql` serving the two real queries in `pricing.py:202-231`.
- `POST /_admin/control` with toggles: `cold_start_ms`, `pricing_api_down`, `endpoint_disabled`, `fail_next_n`.
- `GET /_admin/runs` for the last N `/run` records.

`compose/sim.yml` adds **one** edge service (`qwen3tts-edge`) plus the mock. Not three edges — that requires real stub work for workers that don't have stubs, which is a separate effort.

Justfile adds 2 targets: `just runpod-sim` (boot) and `just runpod-bootstrap` (run all scenarios). No standalone `runpod-sim` CLI — scenarios are Python entrypoints; a parallel CLI is a second tool that will rot.

3 scenarios, each asserting on a JSON endpoint:

| Scenario | Endpoint | Assertion |
|---|---|---|
| `pricing_outage` | `GET /api/workers/{id}` | `cost_basis == CACHED` after `pricing_api_down=true`; edge stays HEALTHY |
| `gpu_switch` | `GET /api/workers/{id}` | after `PATCH /endpoints/{id} --gpu-id A40`, `cost_per_hour` updates within `cache_ttl_s` |
| `cold_start` | `GET /api/workers/{id}/health` | `status == BOOTING` during `cold_start_ms` window, then `HEALTHY` |

### 7.7 Critical gap

> The `runpod` Python SDK does not honor `RUNPOD_BASE_URL` in all paths. `_runpod_client.py:39-43` calls `runpod.Endpoint(endpoint_id)` with no base URL. A local mock at `127.0.0.1:8999` cannot be reached by the real edge without either DNS/host aliasing (`/etc/hosts` in the container pointing `api.runpod.io` -> `127.0.0.1`) or monkey-patching `_open_endpoint` in the harness. This must be spelled out in the harness README *before* any scenario is written; otherwise "tests pass locally, fail in CI" debugging.

## 8. The first-run journey test (Phase 3b)

### 8.1 Goal

> The first-run journey test exists to exercise the **deployment path** that the runtime simulation doesn't touch: cert generation, env wiring, token orchestration, compose startup, dashboard binding. Concretely: in a fresh checkout with no state, follow the README's Quick Start verbatim and assert that the success criteria (orchestrator + dashboard + at least one worker healthy, registration token round-trips, no security warnings) are met.

### 8.2 Success criteria

1. CI runs the full Quick Start in a clean container in <5 minutes.
2. The test fails loudly with a user-journey-level error message (e.g. "step 4: registration token mismatch between orchestrator and edge"), not a raw docker error.
3. Each step of the Quick Start has a corresponding assertion in the test.
4. A change to the README that breaks the Quick Start is caught by CI.
5. The test does not require a RunPod account, a real HF token, or a real GPU.

### 8.3 CI signal

The first-run test's CI signal is `just first-run --step <N>`. A story with `discovered_via: [first-run, ...]` is mechanically verified when the relevant step:

- Exists at `tests/first_run/test_<step>.py`.
- Contains `STORY_REF: <STORY-ID>` in its docstring.
- Was last green on a commit post-merge of `fixed_in[0]`.
- References the story's `user_journey` text (drift detection).

### 8.4 What the first-run test CAN tell us

- Whether the README's Quick Start command sequence is reproducible.
- Whether the cert regeneration + binding works for a fresh checkout.
- Whether the auto-generated registration token reaches the edge services.
- Whether the dashboard binds to a sane default.
- Whether the stub workers come up healthy and register.

### 8.5 What the first-run test CANNOT tell us (scope-bounding)

- Anything requiring a real RunPod account (runpodctl auth, Network Volume, GPU selection).
- Whether real RunPod cold-start billing matches the dashboard.
- Whether real RunPod API contract changes break the worker SDK.
- Whether the runpod Python SDK's actual behavior matches its documentation.

These require human deployment with feedback (the `user-feedback` discovery channel in §5).

## 9. Onboarding — how to use this rubric

### 9.1 Filing a new story

0. **Boundary check.** Check `docs/code_review/summary.md` and the per-theme files for an open or stale story with overlapping `files:` and `lines:`. If one exists, file a `related:` cross-ref instead of a new story. If the overlap is total, do not file.
1. Pick a theme (`DEPLOY` / `OPS` / `MAINT`).
2. Pick the strongest `discovered_via` channel (§5). If unsure, default to the strongest evidence available.
3. Copy the YAML schema from §3.1. Fill every field.
4. Write the four prose blocks (§3.2). Cite file:line everywhere.
5. Append the story to the theme's `docs/ux_review/<theme>.md` file.
6. Update `docs/ux_review/summary.md` (per-theme grade, top concerns, status counts).

When a swarm finding contains multiple user-visible changes, the filer MUST split into one story per distinct `user_journey`. The prose `Recommendation` MUST NOT combine unrelated journeys; if it does, the filing is invalid and the reviewer rejects it with a `re-file as N stories` comment.

### 9.2 Tackling a story

1. Move story to `in-progress` in a worktree (`git worktree add -b ux-tackle/<story-id>-<slug> .worktrees/ux-tackle-<story-id> master`).
2. Worktree branch is named `ux-tackle/<story-id>-<slug>`. Bundles use `ux-tackle/<theme>-bundle-<N>`. The branch MUST be deleted after merge (post-merge hook enforces).
3. Plan: trivial (≤30 LoC, single file, no new abstraction) → inline; else `superpowers:writing-plans`.
4. **Atomic commit.** The fix is one commit titled `fix(<STORY-ID>): <imperative summary>`. Drive-by cleanups land in separate commits that say so in the body.
5. **Gate.** Implementation MUST pass the per-surface gate table below (or an explicit subset declared in the story's `Verification` block, with justification for each excluded target). Failures abort; the story remains `in-progress`.
6. **PR body** MUST contain: (a) `Closes: <STORY-ID>`, (b) `Journey: <verbatim user_journey>`, (c) `Evidence: <link to harness output or transcript path>`, (d) `Rollback: <one-line revert or feature-flag story>`. Missing fields block merge.
7. **Cross-program trailer.** If the PR also closes a `related:` code-review story, add `Closes-CodeReview: <id>` to the commit message. The `ux-review-tackle` skill does not flip the code-review story's status; the next `code-review-update` scan does.
8. **Verified.** `verified_by` MUST differ from the PR author. For `discovered_via: [simulation | first-run]`, run the harness artifact (`just sim-run <scenario>` or `just first-run --step <N>`); the harness output is the evidence and `last_verified_at.commit` is the harness's last-green SHA. For `discovered_via: [code-review | on-call | audit | user-feedback]`, the verified step is human; the manual session is recorded as a comment on the story with a transcript file path.
9. **Post-merge.** The tackle skill removes the worktree (`git worktree remove ... --force; git branch -d ...`) and runs `just ux-validate` against the merged `master` to confirm the story is `fixed` and the cited files still resolve.

#### Per-surface gate

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

### 9.3 Refreshing the rubric

The `ux-review-update` skill (Phase 2b) refreshes each story against the current commit. It performs **two independent checks**, not one:

1. **File/line re-resolution.** Each story's `files[].path` and `files[].lines` are re-resolved against the current commit. Drift → mark `stale` (concern unchanged, code changed) or `obsolete` (concern no longer valid, code or spec changed).
2. **Journey re-exercise.** Each story's `user_journey` is re-exercised against the harness (`just sim-run` / `just first-run`). For journeys without a harness, a fresh-context subagent re-reads the `user_facing_surface` and asserts the journey is still satisfiable. Journey drift → mark `stale` and append a `drift_note:` line.

File/line passing does not satisfy the journey check. A `verified` story whose `files` appears in any non-`docs(ux-review)` commit diff transitions back to `fixed` automatically; the next tackle or update run is the trigger.

On every refresh, `discovered_via` lists are preserved (a story originally surfaced via `code-review` and later confirmed via `simulation` keeps both, with the strongest first). `fixed_in` placeholders are resolved to commit SHAs whose Conventional Commits scope includes the story ID.

### 9.4 First-time readers

If you've never read this spec before, read sections 1, 1.1, 2, 3.1, 3.2, 3.3, 6, and 9.1. The rest is detail you can come back for.

## 10. Anti-patterns

From the swarm's findings, these are the things to NOT do:

- **Do not file a UX story whose only `files:` and `lines:` overlap an open or stale code-review story.** Use `related:` instead.
- **Do not file a UX story without a `user_journey` that names both a starting state and an ending state.** Stories without a journey are abstract; they don't surface the operator's experience.
- **Do not default `silent: false` for cost / observability / recovery stories.** The whole point of those themes is that the system is silent when it shouldn't be. When in doubt, set `silent: true` and let the reviewer downgrade. The downgrade is a normal PR-time edit during review.
- **Do not propose implementation in `Recommendation`.** Describe the desired behavior; let the implementer pick the approach.
- **Do not skip `Verification`.** A story without a verification is just a complaint. The verification is what makes the story actionable.
- **Do not let a UX `verified` story imply that a `related` code-review story is also `verified`.** The two lifecycles are independent; cross-references are advisory, not transitive.
- **Do not mark a story `verified` with `verified_by: <pr-author>`.** The `verified` step requires a second pair of eyes or a harness artifact.
- **Do not grade on the curve.** A grade is the story's current state, not a delta from a previous grade. "B because the last theme was B" is not a grade.
- **Do not import the code-review `summary.md` per-theme grades into the UX rubric.** The two rubrics grade different things (code quality vs operator experience); mixing the grades hides which program is doing the work.

## 11. Known limitations (deferred to v2)

These were considered during the 5-agent adversarial review (2026-07-24) and deferred deliberately. Each lists the agent argument, the v1 compromise, and the trigger for revisiting.

- **4th theme `SEC`.** The taxonomy skeptic and the code-review-boundery proposed splitting security into its own theme. v1 uses the 3-theme split + `journey_stage: cross_cutting` field; security stories (e.g., the swarm's dashboard-bind story) land in DEPLOY/OPS/MAINT depending on the persona. **Revisit when**: 5+ security stories are filed AND a tie-break dispute arises in 6 months.
- **`error_clarity: 1-5` scale.** The taxonomy skeptic proposed replacing `silent: true|false` with a 1-5 scale to distinguish "no signal" (1) from "opaque error" (2) from "category error" (3) from "named cause" (4) from "named cause + remediation" (5). v1 uses the binary; the §10 anti-pattern defaults `silent: true` for cost/observability/recovery stories. **Revisit when**: 3+ stories have a borderline `silent` value that a reviewer wants to upgrade to "opaque" or downgrade to "category".
- **2D effort axis** (`fix_complexity × verification_complexity`). The taxonomy skeptic proposed splitting effort into implementation and verification, because a 1-line fix in `pricing.py` that requires a new sim scenario is M-effort, not S. v1 uses S/M/L; `Verification` blocks may include the verification cost explicitly. **Revisit when**: ≥2 stories are M-effort solely because of verification work, not implementation.
- **`user_journey` as list of steps.** The taxonomy skeptic proposed structured steps for queryability. v1 uses a prose string with a starting state and an ending state. **Revisit when**: ≥3 stories are missed by sim/first-run scenarios because the journey text is ambiguous.
- **`failure_surface + observed_via` split.** The taxonomy skeptic proposed splitting the 8-value `user_facing_surface` into where the bug lives and where the operator first sees it. v1 uses the single 8-value enum. **Revisit when**: ≥3 stories are ambiguous on which surface they belong to.
- **2D grade** (`grade × coverage`). The taxonomy skeptic proposed splitting grade from coverage so that "0 stories = A" is reported as "Untested" instead. v1 uses 1D grade. **Revisit when**: ≥2 themes have 0 stories and the A grade is misleading.
- **`disputed` status.** The taxonomy skeptic proposed a status for stories where the team cannot agree on whether to fix. v1 uses `wontfix` with a clear `wontfix_reason`. **Revisit when**: a `wontfix` story is contested for ≥1 month without resolution.

## Appendix A — How this spec was produced

v1 (2026-07-24) was produced from a synthesis of:

1. An initial rubric proposal (themes, YAML schema, severity axis).
2. A 5-agent adversarial swarm:
   - First-time deployer (DEPLOY lens; 2/10 score).
   - Returning operator (OPS lens; 3/10 score).
   - On-call maintainer (MAINT lens; 3/10 score).
   - Security + cost reviewer (cross-cutting; 3/10 prod-safety, 2/10 cost predictability).
   - Simulation architect (methodology; cut the original sim proposal by ~50%).

v2 (2026-07-24) was produced from a 5-agent adversarial review of v1:

1. **Cold-reader** (catch ambiguities and missing definitions).
2. **Code-review-boundery** (prevent duplication and process confusion; flagged 4/10 of the swarm's top-10 as fully code-review territory).
3. **Tackle-execution** (find gaps in the `fixed → verified` flow; flagged missing fields, missing gates, missing PR description, missing atomic-commit rule).
4. **CI engineer** (spec must be executable; proposed `just ux-validate` / `just ux-verify` / `just sim-run` Justfile targets; proposed the mechanical `verified` path via harness artifacts).
5. **Taxonomy skeptic** (3 themes + 6 channels + binary silent may be too thin; proposed 4th `SEC` theme, `error_clarity: 1-5`, 2D effort, list-of-steps journey; deferred to v2 in §11).

The 10 stories filed in Phase 1 are the UX-original portion of the swarm's top-10, post-boundary-check. The remaining 50+ findings are filed in Phase 2a.
