# Phase 3b First-Run Journey Design

## Goal

Add a deterministic first-run journey harness for the README Quick Start. The harness must exercise a fresh checkout through certificate generation, Compose startup, registration-token wiring, dashboard binding, and worker registration without requiring RunPod credentials, a real Hugging Face token, or a GPU.

Phase 3b is harness-only. It must not change production behavior or add a parallel deployment path.

## Scope

### In scope

- Add a `tests/first_run/` journey harness with step-oriented tests.
- Add `just first-run --step <N>` and an all-steps `just first-run` command.
- Run the journey from a temporary archive of `HEAD`, not the working tree.
- Execute the README Quick Start command sequence against the real Docker Compose stack.
- Verify certificates, service readiness, dashboard binding, worker registration, and registration-token acceptance/rejection.
- Add a CI workflow for the first-run journey.
- Document the first-run target in the development workflow where appropriate.

### Out of scope

- Changes to production application behavior, Compose services, or authentication semantics.
- Real RunPod, `runpodctl`, Network Volume, Hugging Face, or GPU operations.
- New Python dependencies.
- Product UX fixes or UX-story lifecycle changes.

## Design

### Harness layout

The harness consists of:

- `tests/first_run/conftest.py`: registers `--first-run` and `--step`, skips the Docker journey during ordinary pytest runs, and provides isolated checkout and Compose lifecycle fixtures.
- `tests/first_run/test_1_quick_start.py`: extracts the Quick Start fenced command block, checks the documented command sequence, and performs environment preparation in the isolated checkout.
- `tests/first_run/test_2_compose_start.py`: starts the exact documented `docker compose up --build` command and verifies orchestrator HTTPS and dashboard HTTP readiness.
- `tests/first_run/test_3_success_criteria.py`: verifies dashboard content/status, healthy worker registration, registration-token behavior, and absence of unset/open-registration startup warnings.

The tests are independently selectable by `--step`. Running all steps uses one Compose lifecycle so the journey remains within the CI time budget; running a later step starts the required stack itself.

### Fresh-checkout lifecycle

A session fixture creates a temporary checkout with `git archive HEAD`. This keeps generated files, local certificates, `.env`, and developer state out of the journey. The fixture assigns a unique `COMPOSE_PROJECT_NAME` so concurrent or interrupted runs do not share containers or volumes.

The environment-preparation step follows the README commands:

1. Copy `.env.example` to `.env`.
2. Generate a fresh hexadecimal token with `openssl rand -hex 32` and export it for the Compose process.
3. Start `docker compose up --build` in the temporary checkout.

Compose output is written to a per-run log. The harness polls the host-facing HTTPS orchestrator health endpoint and HTTP dashboard endpoint instead of relying only on raw Docker output.

### Assertions

The journey asserts:

1. The README Quick Start block contains the expected three commands in order.
2. The certificate-init path produces a usable CA and the orchestrator returns `{"status": "ok"}` over HTTPS.
3. The dashboard responds successfully and its status partial reports a connected orchestrator.
4. The authenticated worker listing contains at least one `HEALTHY` worker registered by the stack.
5. A valid registration token is accepted for worker registration, while an invalid token is rejected.
6. Compose startup logs do not contain unset-token or open-registration warnings.

Every assertion includes a step-specific failure message. Startup failures include the captured Compose log excerpt rather than exposing only a subprocess error.

### Cleanup and failure handling

The Compose process and resources are cleaned up in a `finally` path. Teardown sends an interrupt to the foreground Compose process, falls back to termination when needed, and runs `docker compose down --volumes --remove-orphans`. Cleanup failures are suppressed when a journey assertion or startup failure already exists, so teardown cannot replace the useful failure diagnosis.

### Just and CI interfaces

The Justfile adds a variadic `first-run` recipe that invokes pytest with xdist disabled and forwards `--step` arguments. Ordinary `just test` collects the files but skips them unless `--first-run` is supplied, preventing Docker startup from entering the normal unit-test gate.

A dedicated CI workflow installs Python, `uv`, and project dependencies, then runs `just first-run` with a five-minute job timeout. It triggers on the README, Compose, Dockerfile, certificate, Justfile, workflow, and first-run harness paths.

## Constraints

- Use existing Python and standard-library facilities; do not add dependencies.
- Preserve the README command sequence as the source-of-truth deployment path.
- Preserve production TLS and registration behavior.
- Keep diagnostics at the user-journey level.
- Use a unique Compose project and always clean up volumes and orphaned services.
- Keep the implementation limited to Phase 3b harness and CI files.

## Verification plan

Run the following before completion:

```bash
uv run pytest tests/first_run --first-run --step 1 -n 0 -q
uv run pytest tests/first_run --first-run --step 2 -n 0 -q
uv run pytest tests/first_run --first-run --step 3 -n 0 -q
just first-run
just validate
just ux-validate
```

Also run `git diff --check` and confirm the Compose project is removed after the journey.
