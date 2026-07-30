---
program: ux-review
last_updated_date: 2026-07-30
version: 4
initial_review_commit: 59458ba
last_updated_commit: 30aa846d7a0bea64b0589525f27e84d1c43e4ca0
related: docs/code_review/
---

# UX Review Summary

## Per-theme grades

| Theme | Grade | Open stories by severity | Verified | Notes |
|---|---|---|---|---|
| DEPLOY | C | 4 medium, 1 low | 0 | 15 stories; 1 stale and 1 obsolete, with certificate, image, and environment gaps |
| OPS | C | 1 medium, 1 low | 1 | 31 stories; 5 stale and 4 obsolete, with cost, CLI, and durable-dashboard gaps |
| MAINT | D | 5 high, 2 medium | 0 | 19 stories; 9 stale and 2 obsolete, with recovery, retention, and cost gaps |

A theme with 0 stories is graded `—` (untested); this summary has no such themes.

## Top concerns (open and stale stories)

1. **MAINT-001** — No admin endpoints to reap stuck `RUNNING` jobs after orchestrator crash.
2. **MAINT-002** — Failed job cost row does not show GPU or cache age.
3. **MAINT-003** — Certificate expiry is silent.
4. **MAINT-004** — Dev certificate SANs do not cover production hostnames.
5. **MAINT-007** — No token rotation command or audit trail.
6. **MAINT-008** — No stuck-job age filter in `list_jobs`.
7. **MAINT-011** — Successful probes clear the last error.
8. **MAINT-012** — `ACHERON_DATA_DIR` grows without cleanup.
9. **MAINT-014** — Cost tracking uses the lowest available rate rather than the paid rate.
10. **MAINT-015** — Zero pricing silently appears as `STATIC`.
11. **MAINT-016** — Dashboard does not show the running image identity.
12. **MAINT-018** — Existing Redis job records have no schema upgrade path.
13. **OPS-005** — Cost labels lack the requested explanatory distinction.
14. **OPS-032** — `acheron job tail` exposes raw HTTP tracebacks.
15. **OPS-033** — Dashboard detail URLs reload as partial fragments.
16. **DEPLOY-002** — Dev certificate SANs still mismatch compose worker names.
17. **DEPLOY-006** — Qwen3-TTS image guidance still omits FlashAttention installation.
18. **DEPLOY-008** — Certificate generation can overwrite an existing CA.
19. **DEPLOY-010** — TranslateGemma model-switching guidance conflicts with offline mode.
20. **DEPLOY-012** — The shell-local token and compose token configuration remain easy to confuse.
21. **DEPLOY-013** — TranslateGemma storage guidance remains ambiguous.

## Quick wins (S-effort, high-impact)

1. **OPS-032** (S, medium) — Route tail HTTP failures through the structured CLI renderer.
2. **OPS-033** (S, low) — Make job detail URLs resolve to the full dashboard shell.
3. **DEPLOY-013** (S, low) — Clarify container-disk versus model-cache storage.
4. **MAINT-013** (S, medium) — Return the request ID to CLI callers.
5. **MAINT-008** (S, high) — Add an age filter for stuck jobs.
6. **OPS-005** (S, high) — Explain cost basis and distinguish unknown from zero.
7. **DEPLOY-008** (M, medium) — Guard certificate regeneration from overwriting operator material.
8. **MAINT-019** (M, medium) — Evict completed job event buffers.

## Story counts

| Status | Count |
|---|---|
| open | 14 |
| in-progress | 0 |
| fixed | 28 |
| verified | 1 |
| partial | 0 |
| stale | 15 |
| obsolete | 7 |
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

- Refresh performed against `30aa846d7a0bea64b0589525f27e84d1c43e4ca0` using file/line re-resolution and independent journey checks.
- `discovered_via` ordering was preserved; existing verified metadata was not rewritten.
- `fixed_in` placeholders were resolved only where the review evidence supplied a matching Conventional Commit SHA.
- `just first-run`, selected `just first-run --step` checks, all three simulation scenarios, and `just ux-validate` passed during the refresh.
- Open and stale stories remain UX concerns even when related code-review work is tracked separately.
