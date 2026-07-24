---
name: ux-review-perform
description: Generate the Acheron UX review rubric (docs/ux_review/*.md) from the codebase and the swarm's findings. Use when (a) the rubric does not yet exist on the branch, or (b) the user invokes `--rebuild` to regenerate from scratch. Refuses if docs/ux_review/summary.md exists without --rebuild, or if the branch is main/master/develop.
---

# ux-review-perform

Generates the UX review rubric by reading the codebase, applying the spec's boundary check, and producing 4 files: `summary.md`, `deploy.md`, `ops.md`, `maint.md`.

## When to run

- First-time setup: no `docs/ux_review/` directory exists.
- Regeneration: user explicitly invokes `--rebuild`.
- Never run on `main`, `master`, or `develop` (always work on a branch).

## Pre-flight

1. Check `git rev-parse --abbrev-ref HEAD`; abort if branch is `main`/`master`/`develop`.
2. Check `ls docs/ux_review/summary.md`; abort unless absent or `--rebuild` is set.
3. Read `/Users/joaomfauvel/devel/acheron/docs/ux_review/SPEC.md` to load the schema, lifecycle, anti-patterns, and boundary rules.
4. Read `/Users/joaomfauvel/devel/acheron/docs/code_review/summary.md` to load the boundary check (don't file UX stories that duplicate open code-review stories).

## Subagent dispatch

Spawn 3 subagents, one per theme. Each receives:
- The spec (§3.1 YAML, §3.2 prose, §3.3 lifecycle, §10 anti-patterns).
- A focused codebase scope (DEPLOY: README, .env.example, acheron.yaml.example, docker-compose.yml, Justfile, Dockerfile.runpod, workers/*/README.md; OPS: src/acheron/cli.py, src/acheron/api_client.py, dashboard/, src/acheron/shell/api/routes/; MAINT: src/acheron/shell/orchestrator.py, src/acheron/worker_sdk/pricing.py, src/acheron/shell/health.py, scripts/generate_dev_certs.py).
- A list of open code-review stories (so they cross-ref via `related:` rather than re-filing).
- An instruction: produce 12-18 stories per theme, well-formed YAML matching the spec, with `user_journey` that names both a starting state and an ending state, and `silent: true` defaulted for cost/observability/recovery stories.

Each subagent returns the stories as structured markdown. The consolidator writes the 4 files in order: `deploy.md` (DEPLOY-*), `ops.md` (OPS-*), `maint.md` (MAINT-*), then `summary.md` (per-theme grades + top concerns + story counts).

## Post-merge

- The skill is one-shot — running it again requires `--rebuild`.
- After the rubric is on disk, `just ux-validate` should pass on the new files; if not, fix the YAML before merging.
