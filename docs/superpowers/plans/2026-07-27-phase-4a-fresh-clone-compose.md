# Phase 4A Fresh-Clone Compose Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the documented fresh-clone Compose and RunPod-profile setup honor its documented environment contract without requiring hidden build or naming knowledge.

**Architecture:** The edge image will build the Acheron wheel in a Docker builder stage, so both `just build-edge` and direct `docker compose --profile runpod-* up --build` work without a host `dist/` directory. All three real edge services will share the same worker ID/host override contract, while RunPod mock services will be opt-in under a simulation profile. README, worker README, and `.env.example` instructions will use the actual Compose-to-worker environment mapping and current `runpodctl` commands.

**Tech Stack:** Docker Compose, multi-stage Dockerfiles, Just, Markdown, pytest first-run harness, Python 3.14.

## Global Constraints

- Use TDD for executable behavior and opt-in first-run checks before implementation.
- Add no dependencies and no type ignores.
- Preserve production TLS, registration authentication, non-root images, and default local Compose behavior.
- Keep RunPod mock services out of `docker compose --profile runpod-*` and available only through an explicit `sim` profile.
- Keep the README Quick Start commands unchanged; `DEPLOY-012` is a later bundle.
- Do not modify TLS generation, model-image dependencies, or worker runtime behavior in this bundle.
- Use `git archive HEAD` fresh-checkout coverage for README and Compose contract assertions.
- Final metadata must mark `DEPLOY-001`, `DEPLOY-003`, `DEPLOY-004`, `DEPLOY-005`, `DEPLOY-007`, and `DEPLOY-011` fixed, with verification recorded only after merge.

## File map

- Modify `Dockerfile.edge`: build the Acheron wheel in an internal builder stage.
- Modify `docker-compose.yml`: add ID/host overrides for Granite and TranslateGemma and gate RunPod mocks behind `sim`.
- Modify `.env.example`: document all RunPod endpoint IDs and generic edge overrides.
- Modify `README.md`: document RunPod CLI setup, image paths, and the edge build prerequisite/contract.
- Modify `workers/qwen3tts/README.md`, `workers/granite_speech/README.md`, and `workers/translategemma/README.md`: document current `runpodctl` setup and Compose variable mapping.
- Modify `tests/first_run/test_1_quick_start.py` and `tests/first_run/test_2_compose_start.py`: add fresh-checkout documentation and profile-contract assertions.
- Modify `docs/ux_review/deploy.md`: update the six story records and line references after implementation.

## Interfaces

The Compose contract for each real edge service is:

```yaml
ACHERON_WORKER__WORKER_ID: ${ACHERON_WORKER__WORKER_ID:-<service-default>}
ACHERON_WORKER__WORKER_HOST: ${ACHERON_WORKER__WORKER_HOST:-<service-default>}
```

The host-side `.env` names remain:

```text
ACHERON_REGISTRATION_TOKEN
RUNPOD_API_KEY
QWEN3TTS_RUNPOD_ENDPOINT_ID
GRANITE_SPEECH_RUNPOD_ENDPOINT_ID
TRANSLATEGEMMA_RUNPOD_ENDPOINT_ID
ACHERON_WORKER__WORKER_ID
ACHERON_WORKER__WORKER_HOST
```

The SDK-facing names remain inside the Compose service environment; worker READMEs will explicitly show this mapping.

### Task 1: Add fresh-checkout contract tests

**Files:**
- Modify: `tests/first_run/test_1_quick_start.py`
- Modify: `tests/first_run/test_2_compose_start.py`

**Interfaces:**
- Consumes: `FirstRunProject`, `prepared_project`, and Docker Compose's `config --format json` output.
- Produces: opt-in assertions for documented variables, image placeholders, profile service selection, and profile environment interpolation.

- [ ] **Step 1: Add documentation contract assertions**

Extend the existing step-1 test without changing `EXPECTED_QUICK_START_COMMANDS`. Assert that `.env.example` documents `GRANITE_SPEECH_RUNPOD_ENDPOINT_ID`, `TRANSLATEGEMMA_RUNPOD_ENDPOINT_ID`, and the generic worker ID/host overrides. Assert that the top-level image examples use `ghcr.io/<owner>/<repo>/` and that each worker README contains the `runpodctl serverless create` command template and the Compose variable mapping.

- [ ] **Step 2: Add a Compose profile configuration helper in the test**

Add a helper that runs:

```python
subprocess.run(
    ["docker", "compose", *profiles, "config", "--format", "json"],
    cwd=project.checkout,
    env=env,
    check=True,
    capture_output=True,
    text=True,
)
```

Parse the JSON with `json.loads` and return the `services` mapping. Keep this helper in the first-run test module because it is Docker-backed and opt-in.

- [ ] **Step 3: Add the profile and override assertions**

In step 2, assert that `runpod-asr` includes `granite-speech-edge`, excludes both RunPod mock services, and resolves `ACHERON_WORKER__WORKER_ID=asr-edge-2` and `ACHERON_WORKER__WORKER_HOST=custom-asr-host` into the Granite service environment. Assert that the explicit `sim` profile includes `tts-runpod-stub` and `translation-runpod-stub`.

- [ ] **Step 4: Run the opt-in tests and confirm the current checkout fails**

Run:

```bash
uv run pytest --no-cov -n 0 tests/first_run/test_1_quick_start.py tests/first_run/test_2_compose_start.py --first-run -q
```

Expected: the new variable/profile assertions fail because the current Compose and documentation do not yet satisfy them. Existing Quick Start assertions should continue to pass.

### Task 2: Make the edge image self-building and profiles deterministic

**Files:**
- Modify: `Dockerfile.edge`
- Modify: `docker-compose.yml`

**Interfaces:**
- Consumes: the existing workspace `pyproject.toml`, `uv.lock`, `README.md`, and `src/acheron` package.
- Produces: a direct-Compose-safe edge image and a deterministic profile service graph.

- [ ] **Step 1: Add a wheel-builder stage to `Dockerfile.edge`**

Add a builder before the existing `edge` stage:

```dockerfile
FROM python:3.14-slim AS wheel-builder

WORKDIR /build
COPY pyproject.toml uv.lock README.md ./
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY src/acheron/ ./src/acheron/
RUN uv build --package acheron --out-dir /build/dist
```

Change the final image's wheel copy to `COPY --from=wheel-builder /build/dist/acheron-*.whl /tmp/`. Keep the existing non-root user, worker source copies, and entrypoint unchanged.

- [ ] **Step 2: Apply the common ID/host overrides to all real edge services**

Keep the existing Qwen defaults and change Granite and TranslateGemma to use the same interpolation pattern, with defaults matching their service IDs:

```yaml
ACHERON_WORKER__WORKER_ID: ${ACHERON_WORKER__WORKER_ID:-granite-speech-edge}
ACHERON_WORKER__WORKER_HOST: ${ACHERON_WORKER__WORKER_HOST:-granite-speech-edge}
```

Use `translategemma-edge` for both TranslateGemma defaults. Do not change endpoint, token, TLS, price, or model variables.

- [ ] **Step 3: Gate RunPod mock services**

Add `profiles: ["sim"]` to `tts-runpod-stub` and `translation-runpod-stub`. Do not add a profile to local stubs or real edge services. This keeps default Quick Start behavior unchanged and prevents RunPod-profile startup from registering mock workers.

- [ ] **Step 4: Run configuration checks**

Run:

```bash
docker compose --profile runpod-asr config --services
docker compose --profile sim config --services
```

Expected: the first output includes `granite-speech-edge` but not the RunPod mock services; the second includes both RunPod mock services. Then rerun the focused first-run tests from Task 1.

### Task 3: Align deployment documentation and environment examples

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `workers/qwen3tts/README.md`
- Modify: `workers/granite_speech/README.md`
- Modify: `workers/translategemma/README.md`

**Interfaces:**
- Consumes: the Compose variables from Task 2 and the current `runpodctl` CLI syntax.
- Produces: copy-pasteable setup guidance with no `<repo>`/`<owner>` ambiguity.

- [ ] **Step 1: Document all profile endpoint IDs and generic edge overrides**

Add Granite and TranslateGemma endpoint IDs to `.env.example`. Replace the single-profile-only edge comment with a clearly labelled “set one active profile at a time” block showing the generic `ACHERON_WORKER__WORKER_ID` and `ACHERON_WORKER__WORKER_HOST` overrides and the defaults for each profile. Keep secrets commented and do not put generated values in the file.

- [ ] **Step 2: Add RunPod CLI prerequisites to the top-level README**

In the RunPod deployment section, document the non-root Linux installation and authentication commands:

```bash
mkdir -p ~/.local/bin
wget --quiet --show-progress https://github.com/runpod/runpodctl/releases/latest/download/runpodctl-linux-amd64 -O ~/.local/bin/runpodctl
chmod +x ~/.local/bin/runpodctl
export PATH="$HOME/.local/bin:$PATH"
runpodctl config --apiKey "$RUNPOD_API_KEY"
```

Document network-volume creation with:

```bash
runpodctl network-volume create --name "acheron-hf-cache" --size 30 --data-center-id "<data-center-id>"
```

Document endpoint creation from an already-created template with:

```bash
runpodctl serverless create --template-id "<template-id>" --gpu-id "<gpu-id>"
```

State which values are copied into `.env`, where the template image path comes from, and that the template itself is created in the RunPod console. Do not invent an unsupported template-creation command.

- [ ] **Step 3: Standardize GHCR image examples**

Change top-level README image examples from `ghcr.io/<repo>/...` to `ghcr.io/<owner>/<repo>/...`, matching the worker READMEs and CI's `${{ github.repository }}` expansion. Add the explicit instruction to run `just build-edge` before direct edge image use, while explaining that direct Compose `up --build` is self-building after this change.

- [ ] **Step 4: Add the Compose-to-SDK mapping to each worker README**

In each worker README, add a short table stating that Compose maps `ACHERON_REGISTRATION_TOKEN` to `ACHERON_WORKER__REGISTRATION_TOKEN`, `RUNPOD_API_KEY` to `ACHERON_WORKER__RUNPOD_API_KEY`, and that the profile-specific endpoint ID maps to `ACHERON_WORKER__RUNPOD_ENDPOINT_ID`. Keep the existing SDK environment-variable reference for non-Compose deployments.

- [ ] **Step 5: Run documentation contract tests**

Run:

```bash
uv run pytest --no-cov -n 0 tests/first_run/test_1_quick_start.py tests/first_run/test_2_compose_start.py --first-run -q
```

Expected: all fresh-checkout contract tests pass.

### Task 4: Verify builds, journeys, and metadata

**Files:**
- Modify: `docs/ux_review/deploy.md`

**Interfaces:**
- Consumes: the final Compose/docs behavior and implementation commit hash.
- Produces: fixed metadata for `DEPLOY-001`, `003`, `004`, `005`, `007`, and `011`.

- [ ] **Step 1: Validate the direct edge build**

From a clean checkout context, run:

```bash
rm -rf dist
docker build -f Dockerfile.edge -t acheron-worker-edge:phase-4a .
```

Expected: the build succeeds without a host `dist/` directory and the resulting image still has `USER acheron` and the existing `acheron-worker-edge` entrypoint.

- [ ] **Step 2: Exercise the fresh README journey**

Run:

```bash
just first-run
```

Expected: all first-run tests pass, Compose cleanup removes volumes/orphans, and no first-run networks remain.

- [ ] **Step 3: Run project gates**

Run:

```bash
just validate
just ux-validate
just runpod-bootstrap
```

Expected: all quality checks, simulator scenarios, and UX metadata validation pass.

- [ ] **Step 4: Update the six story records**

Refresh file line ranges and issue text where the existing records describe the pre-fix state. Set each story to `status: fixed` and `fixed_in: [pending]` until the implementation commit exists; leave `verified_in` empty until post-merge verification.

- [ ] **Step 5: Commit the bundle atomically**

Stage all implementation, test, documentation, and metadata files and commit:

```bash
git add Dockerfile.edge docker-compose.yml .env.example README.md \
  workers/qwen3tts/README.md workers/granite_speech/README.md \
  workers/translategemma/README.md tests/first_run/test_1_quick_start.py \
  tests/first_run/test_2_compose_start.py docs/ux_review/deploy.md
git commit -m "fix(DEPLOY-001,DEPLOY-003,DEPLOY-004,DEPLOY-005,DEPLOY-007,DEPLOY-011): harden fresh clone deployment"
```

- [ ] **Step 6: Resolve the implementation commit reference**

Replace every `fixed_in: [pending]` with the implementation commit hash and amend without changing the commit message:

```bash
git rev-parse HEAD
git add docs/ux_review/deploy.md
git commit --amend --no-edit
```

- [ ] **Step 7: Run fresh correctness and documentation-staleness reviews**

Review the amended commit in fresh contexts. Resolve valid findings, rerun focused tests and all gates, and do not claim completion until the final outputs are successful.
