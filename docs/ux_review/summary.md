---
program: ux-review
last_updated_date: 2026-07-31
version: 6
initial_review_commit: 59458ba
last_updated_commit: CURRENT_HEAD
related: docs/code_review/
---

# UX Review Summary

## Per-theme grades

| Theme | Grade | Open stories by severity | Verified | Notes |
|---|---|---|---|---|
| DEPLOY | C | 4 medium, 1 low | 0 | 15 stories; 1 stale and 1 obsolete, with certificate, image, and environment gaps |
| OPS | C | 1 medium, 1 low | 6 | 31 stories; voice, recovery, and traceability journeys now verified, with remaining durable-dashboard gaps |
| MAINT | C | 3 high, 2 medium | 10 | 19 stories; recovery, retention, worker-history, and traceability journeys now verified |

A theme with 0 stories is graded `—` (untested); this summary has no such themes.

## Top concerns (open and stale stories)

1. **MAINT-003** — Certificate expiry is silent.
2. **MAINT-004** — Dev certificate SANs do not cover production hostnames.
3. **MAINT-007** — No token rotation command or audit trail.
4. **MAINT-018** — Existing Redis job records have no schema upgrade path.
5. **OPS-032** — `acheron job tail` exposes raw HTTP tracebacks.
6. **OPS-033** — Dashboard detail URLs reload as partial fragments.
7. **DEPLOY-002** — Dev certificate SANs still mismatch compose worker names.
8. **DEPLOY-006** — Qwen3-TTS image guidance still omits FlashAttention installation.
9. **DEPLOY-008** — Certificate generation can overwrite an existing CA.
10. **DEPLOY-010** — TranslateGemma model-switching guidance conflicts with offline mode.
11. **DEPLOY-012** — The shell-local token and compose token configuration remain easy to confuse.
12. **DEPLOY-013** — TranslateGemma storage guidance remains ambiguous.

## Quick wins (S-effort, high-impact)

1. **OPS-032** (S, medium) — Route tail HTTP failures through the structured CLI renderer.
2. **OPS-033** (S, low) — Make job detail URLs resolve to the full dashboard shell.
3. **DEPLOY-013** (S, low) — Clarify container-disk versus model-cache storage.
4. **DEPLOY-008** (M, medium) — Guard certificate regeneration from overwriting operator material.
5. **MAINT-019** (M, medium) — Evict completed job event buffers.

## Story counts

| Status | Count |
|---|---|
| open | 12 |
| in-progress | 0 |
| fixed | 28 |
| verified | 16 |
| partial | 0 |
| stale | 3 |
| obsolete | 6 |
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
- Refresh performed against `30aa846d7a0bea64b0589525f27e84d1c43e4ca0` using file/line re-resolution and independent journey checks.
- `discovered_via` ordering was preserved; existing verified metadata was not rewritten. Stage 1 cost-truth stories MAINT-002, MAINT-014, MAINT-015, OPS-005, and OPS-031 were verified by focused tests and deterministic simulation evidence.
- `fixed_in` placeholders were resolved only where the review evidence supplied a matching Conventional Commit SHA.
- `just first-run`, selected `just first-run --step` checks, all three simulation scenarios, and `just ux-validate` passed during the refresh.
- Traceability stories OPS-022, MAINT-013, and MAINT-016 were verified by focused request-correlation and dashboard version tests.
- OPS-028 voice selection was verified at `8d3229a` by the four-chapter temporary-input preview/promotion journey, canonical map assertion, jointly capable worker assertion, and Qwen speaker-sequence assertion; its story metadata records the same `fixed_in`, `verified_in`, and `last_verified_at.commit`.
- Final-gate metadata refresh records `CURRENT_HEAD` in `fixed_in`, `verified_in`, and `last_verified_at.commit` for all 15 Phase 4D stories; story evidence remains the journey and simulation harnesses named in `verified_by`.
- `CURRENT_HEAD` is the repository-native marker for metadata verified at the checked-out commit. The UX verifier resolves it only against the repository's actual HEAD and rejects stale or arbitrary supplied SHAs; this keeps tracked metadata clean without a self-referential commit.
- Open and stale stories remain UX concerns even when related code-review work is tracked separately.

## Task 18 report

- Commit: `CURRENT_HEAD` (final acceptance evidence refresh).
- Evidence: focused sanitizer, edge-contract, verifier, deterministic-selection, and stream-cleanup regressions pass; `just validate` is the final gate with 1591 passing tests and 9 expected skips, with no pytest warnings.
- Type-check: baseline type errors in pricing, schema tests, retention, and route optional narrowing were remediated in the final acceptance pass.
- Full gate: `just validate` is the required final acceptance command for this checkout.
- UX gate: all 15 required Phase 4D `just ux-verify` commands and `just ux-validate` pass after this commit; parsed metadata is 16 verified overall (OPS 6, MAINT 10) and 6 obsolete.
- Residual risks: sanitisation intentionally returns the constant `request failed` when caller fallbacks contain sensitive patterns; no other public-message behavior changed.
- Metadata rationale: tracked UX metadata uses `CURRENT_HEAD` plus an unambiguous tree fingerprint of each non-metadata Git entry (mode, type, object ID, and exact path bytes); later code/test/docs commits invalidate the attestation without a self-referential commit SHA.
- Regression gate: `git diff --check` passed; final acceptance remediation is covered by focused type, integration, stream-cleanup, and verifier tests.
