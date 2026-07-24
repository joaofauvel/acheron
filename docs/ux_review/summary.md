---
program: ux-review
last_updated_date: 2026-07-24
version: 2
initial_review_commit: 59458ba
last_updated_commit: 59458ba
related: docs/code_review/
---

# UX Review Summary

## Per-theme grades

| Theme | Grade | Open stories by severity | Verified | Notes |
|---|---|---|---|---|
| DEPLOY | C | 1 high, 11 medium, 2 low | 0 | 14 stories; cert, network-volume, runpodctl, env-var gaps |
| OPS | D | 6 high, 13 medium | 0 | 19 stories; dashboard, CLI, cost, BOOTING gaps dominate |
| MAINT | C | 4 high, 11 medium | 0 | 15 stories; cert rotation, data dir cleanup, recovery surface |

A theme with 0 stories is graded `—` (untested); this summary has no such themes.

## Top concerns (high-severity open stories)

1. **MAINT-001** — No admin endpoints to reap stuck `RUNNING` jobs after orchestrator crash. `related: [OBS-001, OBS-014, OBS-015]`.
2. **MAINT-002** — Failed job cost row does not show GPU / cache age. `related: [CORR-008, CORR-040, TYPE-005]`.
3. **MAINT-003** — Cert expiry is silent (no 30/7/0-day warnings).
4. **MAINT-004** — Dev cert SAN list (localhost / 127.0.0.1) breaks production TLS verify.
5. **MAINT-007** — No `acheron token rotate` command and no audit trail.
6. **MAINT-008** — No "stuck > N minutes" filter in `list_jobs`. `related: [MAINT-001]`.
7. **MAINT-009** — BOOTING timeout hard-coded to 600s with no operator countdown.
8. **MAINT-011** — `last_error` wiped on first successful probe.
9. **MAINT-012** — `ACHERON_DATA_DIR` grows monotonically, no `acheron cleanup`.
10. **MAINT-014** — `uninterruptablePrice` is the lowest available rate, not what was paid.
11. **MAINT-015** — `price_source: zero` silently disables cost tracking.
12. **MAINT-016** — Dashboard does not surface the running image SHA / version pin.
13. **MAINT-017** — HF cache + `HF_HUB_OFFLINE=1` + model_id change = silent wrong-weights load.
14. **OPS-001** — Dashboard renders only three read-only tables; no drill-down.
15. **OPS-002** — CLI has no `watch` / `follow` mode.
16. **OPS-003** — CLI surfaces Python class names; no remediation hints. `related: [SEC-006, SEC-012, SEC-019]`.
17. **OPS-004** — `JobResponse` carries no submission params / timestamps. `related: [TYPE-005]`.
18. **OPS-005** — Cost basis labels rendered without explanation. `related: [CORR-008, CORR-040, TYPE-005]`.
19. **OPS-006** — `BOOTING` workers show no countdown. `related: [CORR-012]`.
20. **OPS-007** — "Connected" badge means HTTP 200, not "ready to take jobs".
21. **OPS-008** — No `acheron job cancel`. `related: [OPS-020, OPS-021]`.
22. **OPS-010** — `job status` shows `completed` but no output path. `related: [OPS-028]`.
23. **OPS-013** — Failed step's `worker_id` is invisible in `JobResponse.errors`. `related: [OPS-023]`.
24. **DEPLOY-001** — Asymmetric edge env-var defaults across the three worker profiles.

## Quick wins (S-effort, high-impact)

1. **OPS-003** (S, high) — CLI error message enrichment; 30 LoC in `cli.py:_run`.
2. **OPS-007** (S, high) — Add worker-fleet readiness to `/partials/status`; ~20 LoC.
3. **DEPLOY-001** (S, high) — Symmetrize edge env blocks in `docker-compose.yml`; ~10 LoC.
4. **OPS-004** (S, high) — Add the missing fields to `JobResponse`; ~30 LoC in `core/schemas.py`.
5. **OPS-005** (S, high) — Add a `?` tooltip to cost-basis badges; ~10 LoC in `cost.html`.
6. **OPS-002** (S, high) — Add `--follow` to `acheron job submit`; ~40 LoC in `cli.py`.
7. **OPS-006** (S, high) — Add `booting_since_seconds` to `WorkerResponse` + dashboard render; ~25 LoC.
8. **OPS-008** (S, high) — Add `acheron job cancel` + `POST /jobs/{id}/cancel`; ~50 LoC.
9. **OPS-010** (S, high) — Add `outputs: list[OutputSummary]` to `JobResponse`; ~30 LoC.
10. **OPS-011** (S, medium) — Add `GET /plans/{plan_id}` + `acheron job plan`; ~40 LoC.
11. **OPS-013** (M, high) — Replace `errors: list[str]` with `StepError`; ~60 LoC.
12. **OPS-016** (M, medium) — Add `--dry-run` to `submit` + `POST /jobs:preview`; ~50 LoC.
13. **MAINT-002** (S, high) — Extend `PriceEstimate` with `gpu_type` / `queried_at` / `cache_age_seconds`; ~40 LoC.
14. **MAINT-008** (S, high) — Add `?older_than=` to `GET /jobs`; ~30 LoC.
15. **MAINT-009** (S, high) — Add `booting_since` to `RegisteredWorker`; ~30 LoC.
16. **MAINT-010** (S, medium) — `RedisWorkerStore.register` deletes old hash before re-writing; ~10 LoC.
17. **MAINT-013** (S, medium) — CLI captures `x-request-id` response header; ~20 LoC.
18. **MAINT-015** (S, high) — Add `CostBasis.STUB` (or sub-kind field); ~30 LoC.
19. **DEPLOY-009** (S, low) — Document `ACHERON_OPEN_REGISTRATION` in `.env.example`; ~3 LoC.
20. **DEPLOY-013** (S, low) — Clarify container-disk vs weights in translategemma README; prose only.

## Story counts

| Status | Count |
|---|---|
| open | 50 |
| in-progress | 0 |
| fixed | 0 |
| verified | 0 |
| partial | 0 |
| stale | 0 |
| obsolete | 0 |
| broken-yaml | 0 |
| wontfix | 0 |
| **total filed** | **50** |

## Themes and counts

| Theme | Stories | High | Medium | Low |
|---|---|---|---|---|
| DEPLOY | 14 | 1 | 11 | 2 |
| OPS | 19 | 6 | 13 | 0 |
| MAINT | 17 | 4 | 12 | 1 |

(Some IDs are deliberately skipped — e.g., OPS-026/030/032 were dropped at filing time for being borderline-doc or low-ROI.)

## Notes

- Boundary check applied per §9.1 step 0: 4 of the swarm's original top-10 (the README/Justfile/cert footguns and the dashboard-bind security story) are code-review territory; tracked via `related:` only.
- The 5/10 ambiguous theme classifications from the taxonomy-skeptic's stress test are resolved by `journey_stage: cross_cutting` (deferred from v1's "4th SEC theme" per §11).
- `silent: true` defaulted for all cost/observability/recovery stories per §10; downgraded to `false` only for the pre-warm download story (DEPLOY-014), where the slow download is loud.
- The first tackle bundle (Phase 4) can pick from the 20 quick-wins above; 5 of them are S-effort and shippable in a single PR.
