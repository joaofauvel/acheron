# Remaining UX Review Remediation Bundles

## Status

Draft for written-spec review. The UX rubric was refreshed and committed at `8b9ad64` before this design was written.

## Goal

Close every still-valid UX review story without carrying obsolete findings into implementation. Work is ordered by severity first, then shared topology and cross-cutting dependencies. Each bundle is an independently traceable worktree/PR boundary; stories retain their individual verification lifecycle.

The refreshed rubric contains 12 active stories: 6 open and 6 stale. `DEPLOY-002`, `MAINT-004`, and `MAINT-019` are obsolete and are excluded from all bundles.

## Bundle sequence

| Order | Bundle | Stories, highest priority first | Shared boundary | Primary dependency |
|---:|---|---|---|---|
| 1 | `01-cert-tls` | `MAINT-003` (high), `DEPLOY-008` (medium), `MAINT-005` (medium) | Certificate inspection, safe development-material handling, and server reload across MAINT/DEPLOY | Establish one safe certificate-material contract before warning and reload behavior diverge. |
| 2 | `02-token-auth` | `MAINT-007` (high), `MAINT-006` (medium), `DEPLOY-012` (medium) | Registration-token source of truth, Compose interpolation, edge distribution, and rotation | Define one credential lifecycle before implementing auto-mint, persistence, or rotation separately. |
| 3 | `03-redis-schema` | `MAINT-018` (high) | Redis job serialization/deserialization and persisted-record compatibility | Legacy records must remain visible before later maintenance changes rely on them. |
| 4 | `04-ops-cli` | `OPS-015` (medium), `OPS-032` (medium) | API response projection, API client, CLI rendering, and operator remediation | Stabilize the capability/error response contract before adding presentation-specific behavior. |
| 5 | `05-translategemma-docs` | `DEPLOY-010` (medium), `DEPLOY-013` (low) | TranslateGemma offline model/cache guidance and storage topology | State the cache and disk contract once, then align model-switching instructions with it. |
| 6 | `06-dashboard` | `OPS-033` (low) | HTMX history, dashboard shell routing, and durable detail URLs | Preserve partial navigation while making browser reload/share behavior resolve through the shell. |

The story order within a bundle is the priority order. The implementation plan may execute a lower-severity foundation first when the shared contract requires it, but it must preserve the bundle boundaries and explain the dependency in the task handoff.

## Scope decisions

- `DEPLOY-002` is obsolete because the supported Compose stubs do not enable TLS server certificates; the reported fresh-clone hostname-mismatch journey is not exercised.
- `MAINT-004` is obsolete as a production defect because the documented production path mounts externally managed certificates with the required SANs. A future dev/staging SAN-management feature would require a new, narrowly scoped story.
- `MAINT-019` is obsolete because terminal event history is bounded and oldest entries are evicted, with focused tests covering that behavior.
- `MAINT-007` remains valid, but its implementation must coordinate the orchestrator credential with every worker edge. Rotating only `<data_dir>/.registration_token` is insufficient when Compose supplies static startup tokens.
- `DEPLOY-013` remains valid after being reframed as conflicting container-disk versus HF-cache guidance. The implementation must not invent an unmeasured lower disk floor.

## Implementation boundaries

Each bundle gets one worktree and one PR using the existing UX tackle workflow. The branch uses the dominant persona theme and bundle order, for example `ux-tackle/maint-bundle-01`, `ux-tackle/maint-bundle-02`, `ux-tackle/maint-bundle-03`, `ux-tackle/ops-bundle-04`, `ux-tackle/deploy-bundle-05`, and `ux-tackle/ops-bundle-06`.

A bundle may contain a coordinated change only when the stories share a contract. Otherwise, the implementation plan must keep story-level commits and verification separate inside the bundle worktree. Every behavior change follows TDD and uses a concise conventional commit scope for the story being fixed.

No bundle may:

- reintroduce an obsolete story without filing a new story and evidence;
- claim production SAN management, dynamic token reload, or a container-disk floor that the current product contract does not establish;
- replace a UX journey with only unit-test assertions;
- change verified, wontfix, or unrelated fixed stories as drive-by cleanup.

## Behavior and validation design

### `01-cert-tls`

The certificate lifecycle must distinguish development material from operator-owned certificates, expose certificate expiry before it becomes an outage, and provide the documented rotation path. Safe regeneration is the material-handling foundation; expiry status/warnings and reload behavior consume that contract. The bundle must cover non-destructive regeneration, expiry signals at the stated thresholds, reload success, and continued worker connectivity.

Required evidence includes focused certificate-generation and TLS tests, the certificate surface gate (`just validate`, the relevant first-run step, and manual rotation verification), and a manual journey transcript from an independent verifier.

### `02-token-auth`

The token lifecycle must define the source of truth for Compose, auto-mint, file-backed state, environment-supplied state, worker distribution, rotation, and audit history. A new shell must reuse the same token without duplicate configuration. Rotation is complete only when every worker edge has the new credential and dispatch still works; an orchestrator-only file update is not sufficient.

Required evidence includes Compose interpolation checks, token persistence and redaction tests, rotation/audit tests, edge re-registration or reload coverage, the first-run/Compose gate, and an independent credential-rotation journey transcript.

### `03-redis-schema`

Persisted job records need an explicit schema version and a safe migration/defaulting path. Current records must continue to deserialize, and representative historical records must remain listable and recoverable after an upgrade. Malformed records must fail with the existing typed corruption path without hiding unrelated valid jobs.

Required evidence includes legacy-record fixtures, round-trip tests, corruption handling, `just validate`, and an independent recovery journey.

### `04-ops-cli`

Capability data and structured stream errors must use contracts that preserve the operator-visible information. The typed capability path must carry the model and voice data through route, schema, client, and CLI. Missing-job tail errors must remain concise and non-zero while adding the actionable `Try: acheron jobs` remediation.

Required evidence includes route/client/CLI tests, command-level output checks, `just validate`, and an independent operator transcript for both journeys.

### `05-translategemma-docs`

The worker README and top-level README must agree on offline model switching and storage topology. Model changes must explicitly require prewarming the selected snapshot before an offline restart. Storage guidance must distinguish network-volume HF weights from container-disk image/runtime storage without asserting an unmeasured capacity floor.

Required evidence includes documentation consistency checks, the worker-image/manual build gate where the image behavior is touched, the relevant first-run journey, and an independent deployer transcript.

### `06-dashboard`

The dashboard must preserve in-page partial navigation while making a pushed job-detail URL durable. A direct reload or new session must render the dashboard shell and selected detail, not only the partial fragment.

Required evidence includes route/template tests, browser-level or equivalent navigation checks, `just validate`, the dashboard first-run gate, and an independent operator journey transcript.

## Cross-bundle gates

Every bundle must pass:

1. `just validate`.
2. `just ux-validate` after story metadata is updated.
3. The per-surface gate from `docs/ux_review/SPEC.md`.
4. A fresh-context correctness review and a documentation-staleness review before completion.
5. Story-level `fixed_in`, `verified_in`, `last_verified_at`, and `verified_by` updates only after the corresponding journey evidence exists.

After each bundle merges, refresh the UX rubric against the new commit before starting the next bundle. A story whose cited code changes during a later bundle must be revalidated rather than assumed to remain verified.

## Completion criteria

The remediation program is complete when:

- all 12 scoped stories have passed their user journeys and reached `verified`;
- no active story lacks a bundle or current file/line citations;
- `docs/ux_review/summary.md` reports zero open or stale stories for these themes;
- all bundle-specific gates and the final `just validate`/`just ux-validate` pass;
- obsolete-story resolutions remain documented and no unsupported production behavior was introduced.

## Non-goals

This effort does not add a fourth UX theme, redesign the review schema, implement a general identity system, add provider billing reconciliation, or change unrelated code-review stories. Production certificate management, dynamic token rotation semantics, and exact storage sizing are implemented only to the extent defined by the corrected journeys above.
