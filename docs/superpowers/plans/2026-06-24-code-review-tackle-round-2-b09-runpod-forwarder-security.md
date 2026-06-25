---
bundle: B9
name: RunPod forwarder security (3rd-instance widening)
severity: MIXED
stories: 5
m_effort: 0
main_plan: 2026-06-24-code-review-tackle-round-2.md
---

# B9 — RunPod forwarder security (SEC-014, -015, -016, -017, CORR-020)

> **For agentic workers:** Use the **Common Workflow** from the main plan. Each task is S-effort with coarse detail. Read the full story text in `docs/code_review/operations.md` and `correctness.md` before implementing.

**Bundle summary:** Default the translategemma + granite-speech edge `orchestrator_url` to HTTPS; add `USER acheron` to all Dockerfiles; tighten the RunPod handler's payload validation. Round 1 already fixed the qwen3tts instances (SEC-021, SEC-020); this is the 3rd-instance widening for translategemma and granite-speech.

**Expected commits:** 4-5.

---

## Tasks

### Task 1: SEC-014 + SEC-016 — `worker.edge.yaml` default `orchestrator_url` is HTTP for translategemma and granite-speech

**Files:** `workers/translategemma/worker.edge.yaml`; `workers/granite_speech/worker.edge.yaml`.

**Change:** change `orchestrator_url: http://...` to `orchestrator_url: https://...` (or `${ACHERON_ORCHESTRATOR_URL:-https://...}`). Document the dev override (e.g. `https://orchestrator:8443` for the dev profile).

**Test:** no new test (YAML change); the existing test that loads the YAML and constructs the edge app should still pass. Verify the `WorkerSettings.orchestrator_url` field defaults to HTTPS.

**Commit:** `fix(SEC-014, SEC-016): default translategemma-edge and granite-speech-edge orchestrator_url to https`.

---

### Task 2: SEC-015 — all Docker images run as root (orchestrator, dashboard, worker-stub-base, acheron-worker-edge, worker-runpod)

**Files:** `Dockerfile` (root); `dashboard/Dockerfile`; `Dockerfile.edge`; `Dockerfile.runpod` (per worker).

**Change:** add `RUN useradd --create-home --shell /bin/bash acheron` after the system dependencies are installed; add `USER acheron` before `CMD` or `ENTRYPOINT`. Skip if the base image is already `distroless` or similar non-root.

**Test:** build each image; assert `docker run --rm <image> id` returns `uid=1000(acheron)` (or similar non-zero uid). The CI smoke test for the workers already does this — extend it to all 5 images.

**Commit:** `fix(SEC-015): run all Docker images as non-root user`.

**Note:** this is a cross-cutting fix; consider landing per-image if a single commit is too large for review.

---

### Task 3: SEC-017 — granite-speech runpod image runs as root (no `USER` directive)

**Files:** `workers/granite_speech/Dockerfile.runpod`.

**Change:** same as Task 2 — add `RUN useradd ... acheron` and `USER acheron`. (Round 1's SEC-020 did this for translategemma; this is the granite-speech instance.)

**Test:** see Task 2.

**Commit:** `fix(SEC-017): run granite-speech runpod image as non-root user`.

---

### Task 4: CORR-020 — `make_runpod_handler` silently coerces missing `data` field to empty bytes

**Files:** `src/acheron/worker_sdk/cloud.py`; test.

**Change:** the handler currently does `payload.get("data", b"")`; replace with an explicit check: if `"data" not in payload`, raise `WorkerError("RunPod payload missing 'data' field")`.

**Test:** call the handler with `payload={"model": "x"}` (no `data`); assert `WorkerError` is raised with the expected message.

**Commit:** `fix(CORR-020): raise WorkerError when RunPod payload is missing data field`.

---

## Bundle summary

- **Stories:** 5 (all S).
- **Commits:** 4-5 (Tasks 1 + 2 may each be 1-2 commits depending on Docker image count).
- **Cross-bundle:** B7's SEC-013 (API key in URL) is a related security fix in the same module; the two can land in any order.
- **Surface to user if:** any of the 5 Dockerfiles use a base image that doesn't support `useradd` (e.g. `scratch`, `distroless`), or the existing test fixture for image build expects a root user.
