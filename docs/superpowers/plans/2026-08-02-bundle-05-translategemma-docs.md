# TranslateGemma Documentation Bundle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `05-translategemma-docs` for `DEPLOY-010` and `DEPLOY-013`: align offline model-switching instructions with the actual HF cache topology and remove conflicting container-disk guidance without inventing a numeric floor.

**Architecture:** Runtime files remain the source of truth and are not behaviorally changed: the image sets offline mode, startup loads from the local cache, and `HF_HOME` is on the network volume. Both READMEs will describe the same two-resource model—network-volume model snapshots versus container-disk image/runtime storage—and the first-run suite will assert documentation consistency.

**Tech Stack:** Markdown, Dockerfile/Compose configuration, Python first-run tests, Hugging Face CLI instructions, Justfile gates.

## Global Constraints

- Do not change runtime model-loading behavior for this documentation bundle.
- Do not claim the next offline cold start downloads an uncached model.
- Do not publish a new numeric container-disk minimum without a measured image/runtime budget.
- Keep `HF_HOME=/runpod-volume/huggingface-cache` and the standard Hugging Face cache layout consistent.
- Distinguish the edge/Compose model setting from the remote RunPod template’s worker model setting.
- Run documentation consistency, build, first-run, `just validate`, `just ux-validate`, and independent deployer evidence before verification.

## File Map

- `workers/translategemma/README.md` — offline model prewarm/switching and resource guidance.
- `README.md` — top-level RunPod template/cache guidance.
- `tests/first_run/test_1_quick_start.py` — consistency assertions and story reference.
- `.github/workflows/first-run.yml` — documentation path trigger.
- `workers/translategemma/Dockerfile.runpod`, `handler.py`, worker YAMLs, and `docker-compose.yml` — read-only source-of-truth checks; modify only comments/config if the final docs require it.
- `docs/ux_review/deploy.md`, `docs/ux_review/summary.md` — metadata after evidence.

## Tasks

### Task 1: Capture the current runtime contract in failing documentation checks

**Files:**
- Test: `tests/first_run/test_1_quick_start.py`

- [ ] Add assertions that `Dockerfile.runpod` contains `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, and `HF_HOME=/runpod-volume/huggingface-cache`.
- [ ] Add `STORY_REF: DEPLOY-010` to the relevant first-run test docstring/function.
- [ ] Add `test_translategemma_docs_require_prewarm_for_offline_model_switch`; assert the switching section contains a prewarm command and does not claim automatic offline re-download.
- [ ] Add `test_translategemma_docs_separate_network_volume_and_container_disk`; assert both READMEs name the cache path and do not contain the conflicting “container disk ≥10GB/≥30GB because snapshot is 26GB” claims.
- [ ] Run `just first-run --step 1` or its focused Python test; confirm the new checks fail against current documentation.

### Task 2: Correct offline model-switching instructions

**Files:**
- Modify: `workers/translategemma/README.md`

- [ ] Rewrite the one-time RunPod setup section to distinguish network-volume cache, offline image, cloud template variables, and edge/Compose variables.
- [ ] Replace the current “next cold start re-downloads” statement with an explicit prewarm procedure:

```bash
export HF_HOME=/runpod-volume/huggingface-cache
HF_HUB_ENABLE_HF_TRANSFER=1 huggingface-cli download google/translategemma-4b-it
```

- [ ] Explain that the selected snapshot must be present before an offline restart/redeploy.
- [ ] State that the remote RunPod template must receive `ACHERON_WORKER__MODEL_ID=google/translategemma-4b-it` and the edge/Compose model value must remain aligned for advertised capabilities.
- [ ] Keep the existing 12b prewarm command and cache path in the same documented format.
- [ ] Run the focused documentation tests and verify no wording promises network access during offline startup.
- [ ] Commit with `git commit -m "fix(DEPLOY-010): document offline model switching"`.

### Task 3: Separate container-disk and network-volume guidance

**Files:**
- Modify: `workers/translategemma/README.md`
- Modify: `README.md`

- [ ] Remove the worker README’s “container disk ≥30 GB (snapshot ~26GB)” statement.
- [ ] Remove the top-level README’s unsupported “container disk at least 10 GB” numeric minimum.
- [ ] Explain that selected model weights live on `/runpod-volume/huggingface-cache` and consume network-volume storage.
- [ ] Explain that container disk holds image layers, installed runtime, and working files; direct operators to size it from the measured image/runtime footprint without publishing an unmeasured minimum.
- [ ] Keep the network-volume prewarm section separate from template container-disk configuration.
- [ ] Run the documentation consistency tests and `git diff --check`.
- [ ] Commit with `git commit -m "fix(DEPLOY-013): clarify TranslateGemma storage topology"`.

### Task 4: Trigger first-run validation for worker documentation

**Files:**
- Modify: `.github/workflows/first-run.yml`
- Test: `tests/first_run/test_1_quick_start.py`

- [ ] Add `workers/translategemma/README.md` to the pull-request path filter for the first-run job.
- [ ] Include `workers/translategemma/Dockerfile.runpod` in the filter only when the bundle changes its comments or runtime contract; do not broaden the trigger to all worker files.
- [ ] Run the workflow’s local configuration/lint check used by the repository and `just first-run --step 1`.
- [ ] Commit with `git commit -m "ci(DEPLOY-010): validate TranslateGemma documentation changes"`.

### Task 5: Run deployer/build evidence and update UX metadata

**Files:**
- Modify: `docs/ux_review/deploy.md`
- Modify: `docs/ux_review/summary.md`

- [ ] Run `just validate`, `just first-run --step 1`, `just first-run`, `just build-worker translategemma`, `docker compose --profile runpod-translation config --format json`, and `just ux-validate`.
- [ ] Independently follow the 12b prewarm and 4b switch procedure with the documented network volume; verify the selected model loads offline without relying on an implicit download.
- [ ] Independently verify that the documentation gives separate container-disk and network-volume instructions without a conflicting numeric floor.
- [ ] Refresh DEPLOY-010/DEPLOY-013 citations, set `fixed_in`, and set verification fields only after the deployer evidence exists; retain `bundle: 05-translategemma-docs`.
- [ ] Run `just ux-verify DEPLOY-010`, `just ux-verify DEPLOY-013`, and `git diff --check`.
- [ ] Commit with `git commit -m "docs(ux-review): close TranslateGemma documentation bundle evidence"`.

## Completion Gate

- [ ] No README claims automatic download during offline startup.
- [ ] Both READMEs agree on cache path and separate network-volume weights from container disk.
- [ ] No unmeasured numeric container-disk floor is introduced.
- [ ] Documentation checks, build/first-run gates, UX verification, and fresh-context docs review pass.
