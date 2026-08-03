---
theme: DEPLOY
last_updated_date: 2026-08-02
version: 6
---

# DEPLOY

**Grade**: C (3 medium + 1 low unresolved story)
**Calibration target**: a developer who has used Docker but never used RunPod, given 1 day, should succeed without help.

## DEPLOY-001 — Asymmetric edge env-var defaults across the three worker profiles

```yaml
---
id: DEPLOY-001
title: Edge env-var defaults are asymmetric; granite/translategemma profiles do not support a WORKER_HOST override
status: fixed
severity: high
effort: S
discovered_via: [code-review, first-run]
user_facing_surface: compose
silent: true
journey_stage: t0
user_journey: "Deployer copies `.env.example` to `.env`, sets `ACHERON_WORKER__WORKER_HOST=asr-edge-2` to retarget the ASR edge, runs `docker compose --profile runpod-asr up -d`, sees the granite-speech-edge container register with `worker_host=granite-speech-edge` (the hardcoded default), not `asr-edge-2`."
files:
  - path: docker-compose.yml
    lines: 173-208
  - path: docker-compose.yml
    lines: 210-243
  - path: docker-compose.yml
    lines: 245-279
related: [DX-005, DX-006]
fixed_in: [f4a2811]
verified_in: []
last_verified_at: {}
verified_by: ""
---
```

**Issue (historical).** `docker-compose.yml:179-180` (qwen3tts-edge service) wires the operator override as `${ACHERON_WORKER__WORKER_ID:-qwen3tts-1}` and `${ACHERON_WORKER__WORKER_HOST:-qwen3tts-edge}`. The same pattern is **not** used for `granite-speech-edge` (`:213-223`) or `translategemma-edge` (`:247-258`): both hardcode `WORKER_HOST` and do not surface an `ACHERON_WORKER__WORKER_HOST` override. The `${...:-...}` fallback pattern is the deployer's contract for "override this without rebuilding the image"; the asymmetry means a deployer retargeting the ASR or translation edge via `.env` silently gets the default.

**Why it matters.** This is the deployer's #1 lever: a deployer who runs the full RunPod pipeline on a custom host (k8s, a different orchestrator topology, a multi-host compose) needs every edge service to honor the same env-var contract. The current shape means the qwen3tts deploy is portable and the other two are not, with no signal.

**Recommendation.** Apply the same `${ACHERON_WORKER__WORKER_ID:-<default>}` / `${ACHERON_WORKER__WORKER_HOST:-<default>}` pattern to all three edge services. Update `.env.example` with the three new override lines. Update the README's "Edge Worker Proxy Setup" section.

**Verification.** With `ACHERON_WORKER__WORKER_HOST=custom-host` set in `.env`, `docker compose --profile runpod-asr up -d` and `docker compose --profile runpod-translation up -d` register their respective edges with `worker_host=custom-host`. The qwen3tts profile (already correct) continues to work. The orchestrator's `GET /workers` shows the overridden host for all three.

## DEPLOY-002 — Dev cert CN/SAN list does not match compose worker hostnames

```yaml
---
id: DEPLOY-002
title: Dev cert CN/SAN list does not match the compose worker hostnames; orchestrator→stub TLS handshakes fail on a fresh clone
status: obsolete
severity: high
effort: S
discovered_via: [code-review, first-run]
user_facing_surface: certs
silent: true
journey_stage: t0
user_journey: "Deployer runs `cp .env.example .env && export ACHERON_REGISTRATION_TOKEN=… && docker compose up --build` and `docker compose ps` shows orchestrator + four local stubs healthy. Deployer runs `acheron job submit book.epub --src en --dest es`. The job hangs in EXTRACTION→PACKAGING because no TTS/ASR/translation step ever starts; the orchestrator log shows `ssl.SSLCertVerificationError: Hostname mismatch, certificate is not valid for 'tts-local-stub'` against the local stub service."
files:
  - path: scripts/generate_dev_certs.py
    lines: 21-27
  - path: scripts/generate_dev_certs.py
    lines: 120-128
  - path: docker-compose.yml
    lines: 89-174
related: []
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
---
```

**Resolution.** The default Compose stub services do not enable TLS server certificates; they set `SSL_CERT_FILE` only, while the TLS integration tests explicitly provide certificate and key files. The reported hostname-mismatch journey is therefore not exercised by the supported fresh-clone path. Keep this concern closed unless Compose stubs become TLS-enabled.

## DEPLOY-003 — `just build-edge` and `docker compose --profile runpod-* up --build` fail on a fresh clone

```yaml
---
id: DEPLOY-003
title: "`just build-edge` and `docker compose --profile runpod-* up --build` fail on a fresh clone because `dist/acheron-*.whl` is not built yet"
status: fixed
severity: high
effort: S
discovered_via: [code-review, first-run]
user_facing_surface: compose
silent: true
journey_stage: t0
user_journey: "Deployer follows the README's 'Edge Worker Proxy Setup' section and runs `docker compose --profile runpod-tts up --build`. The qwen3tts-edge build step fails with `ERROR: failed to solve: failed to compute cache key: failed to calculate checksum of ref ...: could not process file '/dist/acheron-*.whl': stat dist/acheron-*.whl: no such file or directory`. Deployer reads the error, greps for `dist`, and discovers they need to run `just build-worker` (or `uv build --package acheron --out-dir dist`) before the edge build can succeed."
files:
  - path: Dockerfile.edge
    lines: 10-26
  - path: README.md
    lines: 231-243
  - path: docker-compose.yml
    lines: 173-176
related: [DX-006]
fixed_in: [f4a2811, a953d4d]
verified_in: []
last_verified_at: {}
verified_by: ""
---
```

**Issue (historical).** `Dockerfile.edge:18` does `COPY dist/acheron-*.whl /tmp/`, and the three `workers/*/Dockerfile.runpod` files do the same. A fresh clone has no `dist/` directory. The `Justfile` `build-worker` recipe (lines 47-49) correctly runs `uv build --package acheron --out-dir dist` first, but the `build-edge` recipe (lines 52-53) does NOT.

**Why it matters.** Both the documented `just build-edge` (README:84) and `docker compose --profile runpod-tts up --build` (README:215-218) fail on a fresh clone with a build error that does not point at the missing `dist/` step. Cost: 5-15 min to discover that `uv build` must run first.

**Recommendation.** Make `Justfile:52-53` mirror `build-worker` by prepending `uv build --package acheron --out-dir dist` to the `build-edge` recipe. Add a top-of-README warning: "before `docker compose --profile runpod-* up --build`, run `just build-worker <name>` or `just build-edge` to populate `dist/`."

**Verification.** On a fresh clone, `just build-edge` succeeds without manual `uv build` interleaving. `docker compose --profile runpod-tts up --build` succeeds end-to-end. The `dist/` directory exists before any Docker build step.

## DEPLOY-004 — README documents values but never commands; first-time RunPod deployer stuck at step 1

```yaml
---
id: DEPLOY-004
title: README documents the *values* a deployer must set (endpoint ID, network volume ID) but never the *commands* that produce them; a first-time RunPod deployer cannot get past "RunPod Serverless Deployment" without help
status: fixed
severity: high
effort: M
discovered_via: [code-review, first-run, user-feedback]
user_facing_surface: quickstart
silent: true
journey_stage: t0
user_journey: "Deployer reads README.md:171-199 ('RunPod Serverless Deployment') and the qwen3tts/README.md:11-29 'RunPod Serverless setup' steps. The deployer is told to 'create a network volume', 'create a RunPod serverless template', and 'create a serverless endpoint' with `ghcr.io/<repo>/acheron-qwen3tts-runpod:<tag>`, then 'note the endpoint ID' and set `QWEN3TTS_RUNPOD_ENDPOINT_ID=<id>` in `.env`. None of these steps link to a runpodctl command, a RunPod UI path, or a network volume ID retrieval path."
files:
  - path: README.md
    lines: 172-222
  - path: .env.example
    lines: 17-37
  - path: workers/qwen3tts/README.md
    lines: 11-60
  - path: workers/granite_speech/README.md
    lines: 11-64
  - path: workers/translategemma/README.md
    lines: 11-65
related: []
fixed_in: [f4a2811, 0cb3bc7, 7e35be6, 804086e]
verified_in: []
last_verified_at: {}
verified_by: ""
feedback_ref: "TBD-pagerduty"
---
```

**Issue (historical).** The README's "RunPod Serverless Deployment" section (lines 171-199) and the per-worker READMEs all describe *what* the deployer must do but never the *how*. There is no `runpodctl` command in any README, no link to the RunPod UI's network-volume creation path, and no example of what a network-volume ID or endpoint ID looks like. The Prerequisites at line 20 mention `runpodctl` exists but don't tell the deployer to install it.

**Why it matters.** A first-time deployer who has used Docker but never used RunPod is the calibration target. The "1 day, succeed without help" bar is broken at t0 by this gap.

**Recommendation.** Add a "RunPod Prerequisites" subsection at the top of README.md:171 that walks through `runpodctl` install + `runpodctl config` authentication, then a "Create the network volume" subsection that gives the runpodctl command (or the exact UI path). Add the same `runpodctl serverless create` command template to each worker README. Optionally wire into a `just runpod-bootstrap` target that prints the commands and pauses for the operator to paste back the IDs.

**Verification.** A first-time deployer can complete the RunPod setup in under 30 min without opening the RunPod web UI.

## DEPLOY-005 — `ghcr.io/<repo>` vs `ghcr.io/<owner>` placeholder ambiguity

```yaml
---
id: DEPLOY-005
title: "Worker READMEs use `ghcr.io/<owner>/...`; top-level README uses `ghcr.io/<repo>/...`; org deployers see a non-resolvable image path"
status: fixed
severity: medium
effort: S
discovered_via: [code-review]
user_facing_surface: quickstart
silent: false
journey_stage: t0
user_journey: "Deployer in a GitHub org (e.g., `myorg/acheron-fork`) copies the top-level README's `ghcr.io/<repo>/acheron-qwen3tts-runpod:<tag>` placeholder and substitutes `<repo>` with `acheron-fork`, getting `ghcr.io/acheron-fork/acheron-qwen3tts-runpod:<tag>`. RunPod's template creation rejects the image (404 from ghcr.io) because the org's package path is `ghcr.io/myorg/...`."
files:
  - path: README.md
    lines: 213-217
  - path: workers/qwen3tts/README.md
    lines: 6-9
  - path: workers/granite_speech/README.md
    lines: 6-9
  - path: workers/translategemma/README.md
    lines: 6-9
related: [DOC-012]
fixed_in: [f4a2811]
verified_in: []
last_verified_at: {}
verified_by: ""
---
```

**Issue (historical).** README.md:189-191 documents images as `ghcr.io/<repo>/acheron-{...}-runpod:<tag>`. The per-worker READMEs document them as `ghcr.io/<owner>/...`. CI uses `${{ github.repository }}` which is `<owner>/<repo>`, so the actual image path is `ghcr.io/<owner>/<repo>/...` — meaning the top-level README's `<repo>` placeholder is the one that is wrong, not the per-worker READMEs.

**Why it matters.** An org deployer pastes the wrong path into the RunPod template, gets a 404 on first cold start. Cost: 5-15 min of confusion.

**Recommendation.** Standardize on `<owner>` across all four READMEs. README.md:189-191 should read `ghcr.io/<owner>/<repo>/acheron-{...}-runpod:<tag>` (matching CI's `${{ github.repository }}` expansion).

**Verification.** `grep -rn 'ghcr.io/<repo>\|ghcr.io/<owner>' README.md workers/` returns only the `<owner>/<repo>` form.

## DEPLOY-006 — README recommends FlashAttention 2 for Qwen3-TTS but `qwen3tts/Dockerfile.runpod` does not install it

```yaml
---
id: DEPLOY-006
title: "Top-level README recommends FlashAttention 2 for Qwen3-TTS to fit 24GB, but `qwen3tts/Dockerfile.runpod` does not install it; deployers OOM on 24GB GPUs or spend 30+ min compiling"
status: obsolete
severity: medium
effort: M
discovered_via: [code-review, first-run]
user_facing_surface: worker-image
silent: true
journey_stage: t0
user_journey: "Deployer follows the top-level README's 'GPU & VRAM Guidance' (line 205) and provisions an L4 GPU (24GB) for the Qwen3-TTS endpoint. First job submission cold-starts the qwen3tts-runpod worker. The handler loads `Qwen3-TTS-12Hz-1.7B-CustomVoice`; without FlashAttention 2, the inference peak memory exceeds 24GB; the worker OOMs. The RunPod endpoint logs the OOM, retries twice with the same result, and marks the job FAILED."
files:
  - path: README.md
    lines: 233-238
  - path: workers/qwen3tts/Dockerfile.runpod
    lines: 33-37
related: [DOC-013]
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
---
```

**Resolution.** The Qwen3-TTS handler now selects PyTorch SDPA with `attn_implementation="sdpa"` instead of requiring FlashAttention 2. The prior FlashAttention-specific memory concern no longer describes the current implementation; this change landed in `55f5fb7` (`fix(docker): use PyTorch SDPA in worker images`).

**Verification.** Read `workers/qwen3tts/handler.py:100-109` and confirm model loading specifies SDPA; no FlashAttention installation is required for this path.

## DEPLOY-007 — `tts-runpod-stub` and `translation-runpod-stub` start unconditionally

```yaml
---
id: DEPLOY-007
title: "`tts-runpod-stub` and `translation-runpod-stub` services start unconditionally; they conflict with the `runpod-tts` and `runpod-translation` profiles if both are active"
status: fixed
severity: medium
effort: S
discovered_via: [code-review, first-run]
user_facing_surface: compose
silent: true
journey_stage: t0
user_journey: "Deployer wants to test the RunPod path against the orchestrator. They run `docker compose --profile runpod-tts up -d` to start the qwen3tts-edge service. The deployer didn't realize that `tts-runpod-stub` (port 8006) and `tts-local-stub` (port 8001) both start as well. The orchestrator now sees TWO TTS workers registered: `qwen3tts-1` (the real edge) and `tts-runpod-stub` (the dev-mode stub). The first job submission hits the stub, returns static mock data, and the deployer is confused why the RunPod path isn't being exercised."
files:
  - path: docker-compose.yml
    lines: 284-340
  - path: README.md
    lines: 231-243
related: []
fixed_in: [f4a2811, a953d4d]
verified_in: []
last_verified_at: {}
verified_by: ""
---
```

**Issue (historical).** `tts-runpod-stub` (docker-compose.yml:276-302) and `translation-runpod-stub` (docker-compose.yml:304-330) have no `profiles:` key, so they start on every `docker compose up`. The orchestrator's worker-selection logic is undefined between the real RunPod edge and the dev stub.

**Why it matters.** The deployer testing the RunPod path ends up with both `qwen3tts-1` and `tts-runpod-stub` registered, and the first job picks one at random.

**Recommendation.** Either gate `tts-runpod-stub` and `translation-runpod-stub` behind a `profiles: ["sim"]` key, or rename them to make the local-vs-runpod distinction obvious in `docker compose ps`.

**Verification.** With profile gating, `docker compose --profile runpod-tts up -d` does NOT start `tts-runpod-stub`. `GET /workers` after the RunPod profile is active shows exactly one TTS worker.

## DEPLOY-008 — `certs-init` service and `just certs` overwrite the entire CA on every run

```yaml
---
id: DEPLOY-008
title: "`certs-init` service and `just certs` overwrite the entire CA on every run, invalidating any external cert trust the deployer has wired up"
status: verified
severity: medium
effort: M
discovered_via: [code-review, first-run]
user_facing_surface: certs
silent: true
journey_stage: t0
user_journey: "Deployer provisions Acheron for production with a real CA-signed cert bundle (Let's Encrypt via cert-manager). They commit the bundle to `./certs/` (or mount it as a volume). Two weeks later they make a small change to the orchestrator config and re-run `just certs` to regenerate the dev certs — which now refuses to overwrite unmarked operator material and preserves the real CA bundle."
files:
  - path: docker-compose.yml
    lines: 23-33
  - path: Justfile
    lines: 48-51
  - path: scripts/generate_dev_certs.py
    lines: 152-247
related: [SEC-001]
bundle: 01-cert-tls
fixed_in: [7c16960, 03deac0, 72dcbb8, e5f338a]
verified_in: [CURRENT_HEAD]
last_verified_at:
  commit: CURRENT_HEAD
  tree: 6f602eea16a00379657eff2fe3247ddc7bcae52a5799c6cf57a2d499efc8ecf1
  date: "2026-08-02"
verified_by: "independent:docs/superpowers/review/bundle-01-cert-tls-independent-verification.md"
---
```

**Issue.** The development generator previously rebuilt the CA and service certificates on every run, including from the Compose `certs-init` gate and the `just certs` recipe.

**Current state.** A fresh run creates `.dev-ca` after publishing a complete bundle. Re-running a complete marked bundle is a no-op; partial or unmarked material is rejected without mutation; `--force` is accepted only for a complete marked development bundle. Compose keeps dependent services behind successful `certs-init` completion.

**Verification.** Independent generator, Compose refusal/reuse, status, replacement, reload, same-PID, healthy API, and HTTP worker-connectivity evidence passed in `docs/superpowers/review/bundle-01-cert-tls-independent-verification.md`.

## DEPLOY-009 — `ACHERON_OPEN_REGISTRATION` env var is read by the orchestrator but undocumented

```yaml
---
id: DEPLOY-009
title: "`ACHERON_OPEN_REGISTRATION` env var is read by the orchestrator but is not documented in `.env.example` or the README's Configuration Reference table"
status: obsolete
severity: low
effort: S
discovered_via: [code-review]
user_facing_surface: compose
silent: true
journey_stage: t0
user_journey: "Deployer who needs to test worker registration locally without dealing with the token copies `.env.example` to `.env`, generates a 32-char token, and runs `docker compose up --build`. The local stubs register successfully. They then try to register a custom worker that they wrote; the registration fails with `401 invalid registration token`. They grep the codebase for the error, find the orchestrator's `deps.py` reads `ACHERON_OPEN_REGISTRATION`, and realize they could have set `ACHERON_OPEN_REGISTRATION=1`."
files:
  - path: .env.example
    lines: 1-36
  - path: src/acheron/shell/api/deps.py
    lines: 25-35
related: [DOC-003]
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
---
```

**Resolution.** The configuration reference now documents `ACHERON_OPEN_REGISTRATION` at `README.md:328`. The `.env.example` omission remains a documentation polish item, but the original operator journey no longer requires source inspection.

**Verification.** A deployer can discover the development-only open-registration switch from the README configuration table.

## DEPLOY-010 — TranslateGemma README's "Switching model variants" contradicts `HF_HUB_OFFLINE=1`

```yaml
---
id: DEPLOY-010
title: "TranslateGemma README's \"Switching model variants\" claim contradicts `HF_HUB_OFFLINE=1` in the Dockerfile; the deployer's edge silently fails to load the 4b variant"
status: open
severity: medium
effort: S
discovered_via: [code-review, first-run]
user_facing_surface: worker-image
silent: true
journey_stage: t0
user_journey: "Deployer follows the translategemma/README.md:79-83 'Switching model variants' section: sets `ACHERON_WORKER__MODEL_ID=google/translategemma-4b-it` in `.env` and restarts the translategemma-edge container. The next cold start on RunPod fails: `HF_HUB_OFFLINE=1` is set in the Dockerfile, the 4b weights are not in the network volume's cache, and `transformers` raises `OSError: model google/translategemma-4b-it not found in local cache and offline mode is enabled`."
files:
  - path: workers/translategemma/Dockerfile.runpod
    lines: 11-14
  - path: workers/translategemma/README.md
    lines: 102-106
related: []
bundle: 05-translategemma-docs
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
---
```

**Issue.** `workers/translategemma/Dockerfile.runpod:11-14` sets `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`. The translategemma README at lines 79-83 says "Set `ACHERON_WORKER__MODEL_ID=google/translategemma-4b-it` and restart the edge container. The next cold start re-downloads the smaller weights into the network volume's HF cache." This claim is false: `HF_HUB_OFFLINE=1` prevents re-download.

**Why it matters.** The "Switching model variants" section is the only guidance a deployer has. Following it verbatim produces a hard failure on the next cold start. The failure is silent: the RunPod endpoint returns a generic "worker failed to start" to the orchestrator.

**Recommendation.** Update `workers/translategemma/README.md:79-83` to document the pre-warm step for the 4b model: "After setting `MODEL_ID=google/translategemma-4b-it`, SSH into a RunPod pod with the network volume attached, run `huggingface-cli download google/translategemma-4b-it --local-dir /runpod-volume/huggingface-cache/hub/models--google--translategemma-4b-it` (with `HF_HUB_ENABLE_HF_TRANSFER=1`), then restart the edge."

**Verification.** A deployer who follows the corrected README's "Switching model variants" section can complete the 4b switch in under 5 min (one SSH, one download, one restart).

## DEPLOY-011 — `.env.example` documents qwen3tts env vars but omits granite-speech and translategemma equivalents

```yaml
---
id: DEPLOY-011
title: "`.env.example` documents the qwen3tts edge env vars but omits the granite-speech and translategemma equivalents; the deployer hits a silent default-empty endpoint id"
status: fixed
severity: medium
effort: S
discovered_via: [code-review, first-run]
user_facing_surface: compose
silent: true
journey_stage: t0
user_journey: "Deployer follows the qwen3tts setup in `.env.example:21-28`, then mirrors the pattern for granite-speech: sets `RUNPOD_API_KEY` and `QWEN3TTS_RUNPOD_ENDPOINT_ID`, but does not see `GRANITE_SPEECH_RUNPOD_ENDPOINT_ID` documented. They run `docker compose --profile runpod-asr up -d` and the granite-speech-edge container starts, registers with the orchestrator, but on the first `/execute` returns a RunPod `404 endpoint not found` (because `GRANITE_SPEECH_RUNPOD_ENDPOINT_ID` is the empty string per docker-compose.yml:219's `${GRANITE_SPEECH_RUNPOD_ENDPOINT_ID:-}` fallback)."
files:
  - path: .env.example
    lines: 21-42
  - path: docker-compose.yml
    lines: 219-226
  - path: docker-compose.yml
    lines: 254-265
related: [DOC-010, DOC-011]
fixed_in: [f4a2811, a953d4d]
verified_in: []
last_verified_at: {}
verified_by: ""
---
```

**Issue (historical).** `.env.example:17-36` documents `QWEN3TTS_RUNPOD_ENDPOINT_ID` (line 28) but not `GRANITE_SPEECH_RUNPOD_ENDPOINT_ID` or `TRANSLATEGEMMA_RUNPOD_ENDPOINT_ID`. The compose fallback is `${VAR:-}` (empty string), so the edge container starts with an empty `ACHERON_WORKER__RUNPOD_ENDPOINT_ID` and registers successfully but the first `/execute` call to RunPod fails because the endpoint id is empty.

**Why it matters.** DOC-010 and DOC-011 (both verified) fixed related env-var name drift, but the `.env.example` itself was not extended to cover the other two worker profiles.

**Recommendation.** Add a RunPod Serverless workers block to `.env.example` that documents `GRANITE_SPEECH_RUNPOD_ENDPOINT_ID`, `TRANSLATEGEMMA_RUNPOD_ENDPOINT_ID`, and `TRANSLATEGEMMA_MODEL_ID` (with the default `google/translategemma-12b-it`).

**Verification.** A deployer who copies `.env.example` to `.env`, sets `RUNPOD_API_KEY` and the three endpoint IDs, and runs `docker compose --profile runpod-asr up -d` sees the granite-speech-edge register and the first `/execute` call reach RunPod successfully.

## DEPLOY-012 — Quick Start's `export ACHERON_REGISTRATION_TOKEN=…` is not idempotent across terminals

```yaml
---
id: DEPLOY-012
title: "Quick Start's `export ACHERON_REGISTRATION_TOKEN=…` is not idempotent across terminals; opening a new shell silently breaks `docker compose up`"
status: open
severity: medium
effort: S
discovered_via: [code-review, first-run, user-feedback]
user_facing_surface: quickstart
silent: true
journey_stage: t0
user_journey: "Deployer completes the Quick Start in terminal A (everything works). They close terminal A, open terminal B to make a code change, run `docker compose up --build` to pick up the change, and the orchestrator refuses to start with `ACHERON_REGISTRATION_TOKEN must be set` (the `${ACHERON_REGISTRATION_TOKEN:?…}` at docker-compose.yml:48 short-circuits the compose env interpolation). The deployer re-reads the Quick Start, sees the `export` line, and runs it again."
files:
  - path: README.md
    lines: 24-28
  - path: .env.example
    lines: 4-9
  - path: docker-compose.yml
    lines: 45-49
related: [DX-005]
bundle: 02-token-auth
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
feedback_ref: "TBD-pagerduty"
---
```

**Issue.** README.md:24-27 instructs the deployer to `cp .env.example .env && export ACHERON_REGISTRATION_TOKEN="$(openssl rand -hex 32)" && docker compose up --build`. The `export` puts the token in the shell's environment, but it does NOT write the token to `.env`. The compose `x-*` interpolation `${ACHERON_REGISTRATION_TOKEN:?…}` (docker-compose.yml:48) reads from the shell env, so this works in terminal A. Terminal B is a fresh shell with no env var; `docker compose up --build` short-circuits.

**Why it matters.** A common deployer workflow (edit code, restart compose in a new terminal) is broken by this. Cost: 1-2 min per occurrence.

**Recommendation.** Persist exactly one registration-token value in the project configuration, or use the orchestrator's auto-mint path, so a new shell reuses the same Compose input without appending duplicate assignments. Document the token's source of truth.

**Verification.** A deployer who follows the updated Quick Start in terminal A, closes it, opens terminal B, and runs `docker compose up --build` succeeds without re-exporting. The token persists in `.env` across shell restarts.

## DEPLOY-013 — TranslateGemma README's "container disk ≥ 30 GB" guidance is ambiguous about weights

```yaml
---
id: DEPLOY-013
title: TranslateGemma docs conflate container-disk and HF-cache sizing
status: open
severity: low
effort: S
discovered_via: [code-review]
user_facing_surface: worker-image
silent: true
journey_stage: t0
user_journey: "Deployer reads the TranslateGemma worker README and the top-level README before creating a RunPod template. One document says container disk is ≥30 GB because the snapshot is ~26GB, while the other says container disk is ≥10 GB; the deployer cannot tell whether model weights belong on the container disk or the network volume."
files:
  - path: workers/translategemma/README.md
    lines: 24-49
  - path: workers/translategemma/Dockerfile.runpod
    lines: 62-64
  - path: README.md
    lines: 250-250
related: [DOC-013]
bundle: 05-translategemma-docs
fixed_in: []
verified_in: []
last_verified_at: {}
verified_by: ""
---
```

**Issue.** `workers/translategemma/README.md:24-49` describes the model snapshot beside a container-disk recommendation, while `workers/translategemma/Dockerfile.runpod:62-64` places the HF cache on `/runpod-volume/huggingface-cache`. The top-level README separately gives a 10GB container-disk value, so the documents conflate the network-volume weights with image and runtime storage.

**Why it matters.** A deployer cannot translate the conflicting guidance into separate `containerDiskInGb` and network-volume allocations before creating the endpoint.

**Recommendation.** State the container-disk requirement and HF-cache/network-volume requirement separately, and explain that the model weights consume the network volume rather than the container disk. Do not claim a lower disk floor without a measured image/runtime budget.

**Verification.** The worker and top-level READMEs agree on the two storage resources, identify where the weights live, and let a deployer configure both values without inference from conflicting numbers.

## DEPLOY-014 — Top-level README's pre-warm lacks `HF_HUB_ENABLE_HF_TRANSFER=1`

```yaml
---
id: DEPLOY-014
title: "Top-level README's \"Network Volume for HF cache\" pre-warm uses `huggingface-cli download` without `HF_HUB_ENABLE_HF_TRANSFER=1`; the 26GB TranslateGemma download takes 4x longer than the worker READMEs promise"
status: fixed
severity: medium
effort: S
discovered_via: [code-review, first-run]
user_facing_surface: quickstart
silent: false
journey_stage: t0
user_journey: "Deployer follows README.md:177-181 'Network Volume for HF cache' and runs `huggingface-cli download google/translategemma-12b-it` to pre-warm the 26GB weights. The download takes 90+ minutes on a typical 1 Gbps connection (vs. the 20 minutes the worker README implies via `HF_HUB_ENABLE_HF_TRANSFER=1`). The deployer reads the worker README, sees the parallel `hf-transfer` instruction, and switches mid-pre-warm."
files:
  - path: README.md
    lines: 193-207
  - path: workers/qwen3tts/README.md
    lines: 24-38
  - path: workers/granite_speech/README.md
    lines: 24-41
  - path: workers/translategemma/README.md
    lines: 24-43
related: []
fixed_in: [a953d4d, 0cb3bc7]
verified_in: []
last_verified_at: {}
verified_by: ""
---
```

**Issue (historical).** README.md:175-183 ("Network Volume for HF cache") gives the deployer three `huggingface-cli download` commands for the qwen3tts, granite-speech, and translategemma weights. None of them set `HF_HUB_ENABLE_HF_TRANSFER=1` or mention `pip install hf-transfer`. The worker READMEs DO mention the speedup flag. The top-level README's pre-warm step is the canonical first reference, and it's missing the speedup.

**Why it matters.** The translategemma 26GB download is the worst case: 4x slower without hf-transfer, which is 70+ extra minutes for a deployer on a 1 Gbps connection.

**Recommendation.** Update README.md:175-183 to mirror the worker READMEs' pre-warm pattern: add `pip install "huggingface_hub[cli]" hf-transfer` and prefix each `huggingface-cli download` with `HF_HUB_ENABLE_HF_TRANSFER=1`.

**Verification.** A deployer who follows the updated top-level README's pre-warm step completes the 26GB TranslateGemma download in ~20 minutes. `grep -n 'HF_HUB_ENABLE_HF_TRANSFER' README.md workers/*/README.md` returns at least one hit in each.

## DEPLOY-015 — Worker README environment names do not match Compose host variables

```yaml
---
id: DEPLOY-015
title: "Worker READMEs show SDK environment names but Compose requires different host-side variables"
status: fixed
severity: high
effort: S
discovered_via: [code-review, first-run]
user_facing_surface: quickstart
silent: true
journey_stage: t0
user_journey: "Deployer follows a worker README, sets ACHERON_WORKER__REGISTRATION_TOKEN and ACHERON_WORKER__RUNPOD_ENDPOINT_ID in .env, and starts the matching Compose profile. Compose actually requires ACHERON_REGISTRATION_TOKEN and a profile-specific endpoint variable, so registration fails or the endpoint remains empty."
files:
  - path: .env.example
    lines: 21-42
  - path: docker-compose.yml
    lines: 219-226
  - path: docker-compose.yml
    lines: 254-265
  - path: workers/qwen3tts/README.md
    lines: 46-56
  - path: workers/granite_speech/README.md
    lines: 50-60
  - path: workers/translategemma/README.md
    lines: 41-52
related: [DEPLOY-001, DEPLOY-004, DEPLOY-011]
fixed_in: [f4a2811, a953d4d]
verified_in: []
last_verified_at: {}
verified_by: ""
---
```

**Issue.** The worker READMEs describe SDK-facing `ACHERON_WORKER__*` names, while the repository Compose services consume host-side `ACHERON_*` and profile-specific endpoint variables before mapping them into SDK settings. The mismatch causes a copy-paste deployment to fail authentication or silently use an empty endpoint.

**Why it matters.** The worker README is a first-time deployer's most specific setup guide. A correct SDK configuration can still fail when copied into the repository's `.env`.

**Recommendation.** Show the Compose-to-SDK mapping beside the SDK environment reference in every worker README.

**Verification.** Each worker README names both sides of the mapping, and a fresh-checkout Compose config resolves the profile-specific endpoint and registration token into the SDK environment.
