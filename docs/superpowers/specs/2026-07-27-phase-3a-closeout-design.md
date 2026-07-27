# Phase 3a Closeout Design

**Date:** 2026-07-27  
**Status:** Approved for planning

## Goal

Close the Phase 3a runtime-simulation contract from `docs/ux_review/SPEC.md` without changing user-facing product behavior. The simulator must run deterministically against one canonical mock RunPod service, expose the documented per-scenario command, and pass the project and UX-review validation gates.

## Scope

### In scope

- Make `compose/sim.yml` the canonical owner of the mock RunPod service on `127.0.0.1:8999`.
- Change all three scenarios to use that shared service rather than starting private in-process servers.
- Reset simulator state at the beginning of each scenario so `--all` is deterministic.
- Add `just sim-run <scenario>` as the documented per-scenario CI signal.
- Keep `just runpod-bootstrap` as the all-scenarios convenience command.
- Start the simulator from the standalone simulation Compose file so the full deployment's registration-token requirement is not evaluated.
- Add `sim/scenarios/INDEX.md` describing each scenario, endpoint, assertion, and story reference.
- Make gRPC health tests independent of ambient `SSL_CERT_FILE` while preserving production TLS behavior.
- Verify the UX rubric, project validation, simulator startup, individual scenarios, and all-scenario bootstrap.

### Out of scope

- Fixing any UX review story or changing story lifecycle statuses.
- Building the Phase 3b first-run journey test.
- Changing production health, pricing, RunPod, or dashboard behavior except where required to make the harness boundary explicit.
- Rewriting the UX review rubric or changing its discovery-channel metadata.

## Design

### Canonical simulator lifecycle

`compose/sim.yml` provides one `runpod-sim` service with the existing mock implementation and published port `8999`. The scenario modules use the fixed local URL from the Phase 3a specification. They no longer create daemon-thread servers or use scenario-specific ports.

Each scenario begins by calling the mock reset endpoint. This clears endpoint mutations, failure toggles, and recorded runs before assertions start. The all-scenarios runner can therefore execute scenarios in sorted order without state leaking between them.

### Command interface

The Justfile exposes three simulation targets:

- `runpod-sim`: build/start the standalone mock service and wait for its health check.
- `sim-run scenario`: ensure the mock service is available, then execute one named scenario through `sim.run`.
- `runpod-bootstrap`: ensure the mock service is available, then execute `sim.run --all`.

The Python module remains the scenario implementation entry point; the Just targets provide the reproducible CI/operator interface required by the spec.

### Scenario contract

`sim/scenarios/INDEX.md` is the human-readable manifest. Each entry records:

- scenario module;
- story reference;
- mock endpoint or control-plane behavior exercised; and
- JSON-oracle assertion.

The existing `STORY_REF` docstring markers remain the machine-readable linkage. This closeout does not mark those stories fixed or verified.

### Test isolation

The gRPC health tests use an in-process plaintext server. They must explicitly remove ambient CA configuration for that test path so a runner-provided `SSL_CERT_FILE` cannot cause an insecure test server to be contacted through a secure channel. The TLS implementation and its production trust-store behavior remain unchanged.

## Verification plan

1. `just ux-validate`
2. `just validate`
3. `just runpod-sim`
4. `just sim-run pricing_outage`
5. `just sim-run gpu_switch`
6. `just sim-run cold_start`
7. `just runpod-bootstrap`

The closeout is complete only if all commands succeed and the working tree contains no unrelated changes.

## Risks and mitigations

- **Docker availability:** Phase 3a is intentionally a Compose-backed harness; local verification must report Docker as a prerequisite rather than silently falling back to an in-process mock.
- **State leakage:** Every scenario resets the shared mock before exercising its behavior.
- **Environment-dependent TLS tests:** Test-local environment cleanup prevents `SSL_CERT_FILE` from changing the transport selected for the plaintext fixture.
- **Command drift:** The Justfile target and scenario index are validated alongside the existing rubric and project gates.
