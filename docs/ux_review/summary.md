---
program: ux-review
last_updated_date: 2026-08-02
version: 10
initial_review_commit: 59458ba
last_updated_commit: CURRENT_HEAD
related: docs/code_review/
---

# UX Review Summary

## Per-theme grades

| Theme | Grade | Open stories by severity | Verified | Notes |
|---|---|---|---|---|
| DEPLOY | C | 2 medium, 1 low | 1 | 15 stories; certificate material is protected and independently verified, with image, environment, and model-cache gaps remaining |
| OPS | C | 1 low | 6 | 31 stories; typed capability and tail-remediation drift remain alongside durable-dashboard gaps |
| MAINT | C | 0 | 12 | 19 stories; certificate monitoring and reload are independently verified, with schema and token drift remaining |

A theme with 0 stories is graded `—` (untested); this summary has no such themes.

## Top concerns (open and stale stories)

1. **MAINT-007** — Shared registration-token rotation has no safe workflow or audit trail.
2. **MAINT-018** — Existing Redis job records have no schema upgrade path.
3. **MAINT-006** — Compose prevents the documented registration-token auto-mint path.
4. **DEPLOY-010** — TranslateGemma model-switching guidance conflicts with offline mode.
5. **DEPLOY-012** — The shell-local token and Compose token configuration remain easy to confuse.
6. **OPS-015** — Typed capability output omits the model and voice fields.
7. **OPS-032** — `acheron job tail` still lacks missing-job remediation.
8. **DEPLOY-013** — TranslateGemma storage guidance conflates container disk and HF cache.
9. **OPS-033** — Dashboard detail URLs reload as partial fragments.

## Quick wins (S-effort, high-impact)

1. **OPS-032** (S, medium) — Add missing-job remediation to the structured tail error.
2. **MAINT-006** (S, medium) — Make Compose reach the token auto-mint path.
3. **DEPLOY-010** (S, medium) — Correct offline model-switching guidance.
4. **DEPLOY-012** (S, medium) — Persist one Compose token source across shells.
5. **DEPLOY-013** (S, low) — Clarify container-disk versus model-cache storage.

## Remaining tackle bundles

| Order | Bundle | Stories (priority order) | Topology / cross-cutting boundary |
|---:|---|---|---|
| 1 | `01-cert-tls` | `MAINT-003` (high), `DEPLOY-008` (medium), `MAINT-005` (medium) | Certificate inspection, safe material handling, and reload lifecycle across MAINT/DEPLOY |
| 2 | `02-token-auth` | `MAINT-007` (high), `MAINT-006` (medium), `DEPLOY-012` (medium) | Shared registration credential, Compose interpolation, edge distribution, and rotation |
| 3 | `03-redis-schema` | `MAINT-018` (high) | Persisted Redis record compatibility and migration |
| 4 | `04-ops-cli` | `OPS-015` (medium), `OPS-032` (medium) | API/client/CLI operator contracts |
| 5 | `05-translategemma-docs` | `DEPLOY-010` (medium), `DEPLOY-013` (low) | Worker-image offline/cache guidance and storage topology |
| 6 | `06-dashboard` | `OPS-033` (low) | Durable dashboard shell and browser URL routing |

Stories are listed high-to-low within each bundle; bundle order prioritizes high-severity work, then shared topology and cross-cutting dependencies.

## Story counts

| Status | Count |
|---|---|
| open | 4 |
| in-progress | 0 |
| fixed | 27 |
| verified | 19 |
| partial | 0 |
| stale | 4 |
| obsolete | 10 |
| broken-yaml | 0 |
| wontfix | 0 |
| **total filed** | **65** |

## Themes and counts

| Theme | Stories | High | Medium | Low |
|---|---:|---:|---:|---:|
| DEPLOY | 15 | 5 | 8 | 2 |
| OPS | 31 | 10 | 19 | 2 |
| MAINT | 19 | 14 | 5 | 0 |

(Some IDs are deliberately skipped — e.g., OPS-026/030 — while OPS-032/033 and MAINT-018/019 were added from the current-HEAD refresh.)

## Notes

- Task 10 recovery evidence covers stuck-job discovery/reaping, archive and cleanup controls, worker history rendering, and CLI/dashboard filters via `harness:phase-4d-task-10-recovery`.
- Current-head metadata was refreshed after `de02825`; the Dockerfile data-volume fix and code-review metadata changed, but no UX-facing story or citation drift was introduced.
- Current-head journey checks marked DEPLOY-002, MAINT-004, and MAINT-019 obsolete; OPS-015, OPS-032, MAINT-006, and MAINT-018 remain valid stale stories. `discovered_via` ordering and existing verified metadata were preserved.
- `fixed_in` placeholders were resolved only where the review evidence supplied a matching Conventional Commit SHA.
- `just first-run` passed all 9 journey tests after the data-volume fix, and all three simulation scenarios remain green. The refresh updated the 16 `CURRENT_HEAD` tree attestations for verified OPS/MAINT stories; `just ux-validate` passes.
- Traceability stories OPS-022, MAINT-013, and MAINT-016 were verified by focused request-correlation and dashboard version tests.
- OPS-028 voice selection was verified at `8d3229a` by the four-chapter temporary-input preview/promotion journey, canonical map assertion, jointly capable worker assertion, and Qwen speaker-sequence assertion; its story metadata records the same `fixed_in`, `verified_in`, and `last_verified_at.commit`.
- Final-gate metadata refresh records `CURRENT_HEAD` in `fixed_in`, `verified_in`, and `last_verified_at.commit` for all 15 Phase 4D stories; story evidence remains the journey and simulation harnesses named in `verified_by`.
- `CURRENT_HEAD` is the repository-native marker for metadata verified at the checked-out commit. The UX verifier resolves it only against the repository's actual HEAD and rejects stale or arbitrary supplied SHAs; this keeps tracked metadata clean without a self-referential commit.
- Open and stale stories remain UX concerns even when related code-review work is tracked separately. Bundle 01's three stories are now verified by the independent certificate-rotation journey; the remaining bundles are encoded in story `bundle` metadata and contain only the revalidated stories.
- Simulation and first-run artifacts are supplemental evidence for stories whose strongest discovery channel is human; only stories discovered via `simulation` or `first-run` require exact `STORY_REF` and `user_journey` attestation.

## Task 18 report

- Commit: `CURRENT_HEAD` (subject: `docs(ux-review): correct final subject evidence`).
- Evidence: focused credential, registration-transport, multipart, health-deadline, and retention-race regressions pass; `just validate` is the final gate with 1695 passing tests and 9 expected skips, with no pytest warnings.
- Type-check: baseline type errors in pricing, schema tests, retention, and route optional narrowing were remediated in the final acceptance pass.
- Full gate: `just validate` is the required final acceptance command for this checkout.
- UX gate: all 15 required Phase 4D `just ux-verify` commands and `just ux-validate` pass after this commit; parsed metadata is 16 verified overall (OPS 6, MAINT 10) and 6 obsolete.
- Residual risks: sanitisation intentionally returns the constant `request failed` when caller fallbacks contain sensitive patterns; no other public-message behavior changed.
- Metadata rationale: tracked UX metadata uses `CURRENT_HEAD` plus an unambiguous tree fingerprint of each non-metadata Git entry (mode, type, object ID, and exact path bytes); later code/test/docs commits invalidate the attestation without a self-referential commit SHA.
- Regression gate: `git diff --check` passed; final acceptance remediation is covered by focused type, integration, stream-cleanup, and verifier tests. The post-merge current-head attestation refresh is green under `just ux-validate`.
