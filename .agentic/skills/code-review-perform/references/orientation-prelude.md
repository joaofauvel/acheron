# Orientation Prelude

Instructions for the main agent on how to produce a codebase orientation brief that gets handed to every bundle subagent. Produced once per `perform` or `update` run.

## Purpose

Prevent each bundle subagent from re-discovering the codebase layout. Ensures shared structural awareness so subagents catch cross-cutting issues their narrow lens would miss alone.

## Contents (required sections)

The brief is a single markdown file (in-memory; not written to disk) containing:

### 1. Repository at a glance

- Top-level directories with one-line purpose each.
- Branch and HEAD SHA at scan time.
- Notable absences (e.g., "no `tests/` for `core/` package").

### 2. Hexagonal layout summary

- For each `src/<package>/`: list `application/`, `core/`, `infrastructure/` presence.
- Identify port files (`<package>/ports.py`).
- Note the modules that introduce ports vs consume them.

### 3. dbt layer summary

- List the layers present in `models/` (e.g., staging, intermediate, silver, gold, feature). The repo's dbt project lives at the root, not under `dbt/`.
- Count of models per layer.
- Source files (`_sources.yml` etc.) noted.

### 4. Test landscape

- For each top-level `src/<package>/`, presence and rough size of mirror under `tests/`.
- Identify packages with no test mirror.

### 5. Tooling

- Justfile commands available (just `--list` output equivalent).
- `pyproject.toml` highlights: tool sections, dependency groups, key dev dependencies.

### 6. Key entry points

- Scripts/CLI entry points (resolver runs, scripts/, etc.).
- dbt model selection patterns.

## Format

Plain markdown, ≤200 lines. Conciseness matters — the brief is part of every subagent's prompt and inflates token cost when bloated.

## Generation

The main agent generates this brief by reading directly (NOT via subagent dispatch — no recursive subagent overhead). Use a combination of:

- `find . -maxdepth 3 -type d -not -path '*/\.*'` for tree shape
- `git rev-parse HEAD` and `git branch --show-current` for SHA/branch
- `cat pyproject.toml` (relevant sections only)
- `just --list` for command surface
- Lightweight grep for port files: `find src -name 'ports.py'`
- `find models -mindepth 1 -maxdepth 1 -type d` for dbt layers (repo's dbt project is at root)

The brief is regenerated on every `update` run so structural drift is reflected.

## Output

The brief is passed inline to each bundle subagent's prompt under a `## Codebase orientation` section. It is also captured (for traceability) by the consolidator in `summary.md` under a small "Last orientation snapshot" section.
