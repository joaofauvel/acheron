# Phase 4 UX Remediation Bundles

## Status

Approved strategy for the post-Phase-3 UX remediation work. This document defines bundle boundaries and order; it does not implement a story.

## Goals

- Reduce the highest operator and deployment risks first.
- Group stories only when they share a contract, data flow, or user journey.
- Keep each implementation unit independently testable and traceable to its story IDs.
- Preserve the existing gates: `just validate`, `just ux-validate`, `just first-run`, and `just runpod-bootstrap` where applicable.

`OPS-003` is complete in `6992588`. The UX artifact must be refreshed to mark it verified before the next tackle begins.

## Prioritization model

Use risk-first ordering constrained by topology:

1. High-severity deployment and operator risks precede medium and low severity work.
2. A shared foundation precedes stories that consume its contract.
3. Quick, isolated stories break ties between equally dependent work.
4. Bundles contain two to five stories; a six-story bundle is allowed only for one inseparable deployment or recovery contract.
5. Stories remain independently tracked; unrelated stories do not share a bundle merely to reduce PR count.

## Bundle sequence

### Phase 4A — Deployment trust

These bundles protect the fresh-clone and production deployment paths.

1. **Fresh-clone Compose contract** — `DEPLOY-001`, `DEPLOY-003`, `DEPLOY-004`, `DEPLOY-005`, `DEPLOY-007`, `DEPLOY-011`
2. **TLS lifecycle** — `DEPLOY-002`, `DEPLOY-008`, `MAINT-003`, `MAINT-004`, `MAINT-005`
3. **Registration lifecycle** — `DEPLOY-009`, `DEPLOY-012`, `MAINT-006`, `MAINT-007`
4. **Worker image and cache reproducibility** — `DEPLOY-006`, `DEPLOY-010`, `DEPLOY-013`, `DEPLOY-014`, `MAINT-017`

Phase 4A uses the full fresh-checkout journey for deployment-facing changes. Compose cleanup must remain failure-preserving.

### Phase 4B — Safe submission

These bundles make readiness and input validation trustworthy before a long-running job begins.

1. **Readiness contract foundation** — `OPS-007`
2. **Readiness experience** — `OPS-006`, `OPS-019`, `MAINT-009`
3. **Submission preflight** — `OPS-015`, `OPS-018`, `OPS-024`, `OPS-025`, `OPS-029`
4. **Plan preview** — `OPS-011`, `OPS-016`

The readiness contract precedes dashboard countdowns and submission warnings. Capability discovery and input validation precede plan preview.

### Phase 4C — Job visibility and control

These bundles expose the job state needed for diagnosis and recovery.

1. **JobResponse envelope foundation** — `OPS-004`
2. **Job detail and failure attribution** — `OPS-001`, `OPS-010`, `OPS-013`, `OPS-017`, `OPS-023`
3. **Cancellation foundation** — `OPS-008`
4. **Control and recovery** — `OPS-009`, `OPS-020`, `OPS-021`, `OPS-027`
5. **Live monitoring** — `OPS-002`, `OPS-014`

The response envelope precedes dashboard and CLI detail work. Cancellation precedes remediation that tells operators how to stop or retry execution.

### Phase 4D — Cost and long-term operations

These bundles improve cost interpretation and operational recovery after the primary journey is trustworthy.

1. **Cost truth** — `MAINT-014`, `MAINT-015`, `OPS-005`, `OPS-031`, `MAINT-002`
2. **Recovery administration** — `MAINT-001`, `MAINT-008`, `MAINT-010`, `MAINT-011`, `MAINT-012`, `OPS-012`
3. **Traceability and deployed-version visibility** — `OPS-022`, `MAINT-013`, `MAINT-016`
4. **Job-level voice selection** — `OPS-028` as a standalone larger feature

## Implementation policy

Each bundle receives its own focused design and implementation plan. A bundle may be implemented as one coordinated change only when its stories share the same contract; otherwise, its stories are tackled sequentially in the listed order. Every behavior change uses TDD and receives a fresh correctness and documentation-staleness review before completion.

The first implementation target is the **Fresh-clone Compose contract** bundle after the UX artifact refresh. It covers `DEPLOY-001`, `DEPLOY-003`, `DEPLOY-004`, `DEPLOY-005`, `DEPLOY-007`, and `DEPLOY-011`. Its acceptance must include fresh-checkout Compose/profile checks, README and `.env.example` consistency checks, `just validate`, `just ux-validate`, and the relevant first-run journey. The standalone `OPS-007` readiness contract follows in Phase 4B.
