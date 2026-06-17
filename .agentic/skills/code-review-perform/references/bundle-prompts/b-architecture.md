# Bundle B — Architecture

## Themes
ARCH, CFG (medium threshold per `themes-taxonomy.md`)

## Lens

You are reviewing through the lens of system shape — boundaries, layering, dependency direction, and configuration discipline.

- **ARCH**: hexagonal layering violations, port placement, dependency direction (core should not import application or infrastructure), package cohesion, file size/responsibility creep, abstraction quality
- **CFG**: split between user-authored YAML (application concern) and core dataclasses (algorithm input shape) per AGENTS.md, env var sprawl, config-knob hygiene, knobs-without-behavior per project hard rules

## Threshold

Medium.

## Lens questions

For each module read:

1. Does any `core/` module import from `application/` or `infrastructure/`? (Forbidden direction.)
2. Are `ports.py` files placed in the consuming package, not centralized?
3. Are inbound shape Protocols colocated with consumers (e.g., in `core/<pkg>/types.py`) per AGENTS.md?
4. Is each Protocol justified by AGENTS.md criteria (test fake exists, multiple impls, per-env swap, or external import block)?
5. Are there config dataclasses that describe YAML file layout but live in `core/`? Or algorithm-input dataclasses in `application/`?
6. Are there config knobs that don't actually control anything (silent/unexpected behavior per AGENTS.md hard rules)?
7. Are environment variables read in places other than designated config loaders?

## Files to read (path globs)

- `src/forecastest/**/*.py` (full Python tree)
- `pyproject.toml` (for `tool.import-linter` if present, dependency groups)
- `.env.local.example` (env var surface)

Skip: `tests/**`, `models/**`, `macros/**` (architecture is Python-side here; dbt project is at the repo root, not under `dbt/`).

## Output

```json
{
  "bundle": "b-architecture",
  "findings": [
    {
      "prefix": "ARCH",
      "title": "Application module imports infrastructure adapter directly",
      "severity": "high",
      "effort": "M",
      "files": [
        {"path": "src/forecastest/application/training/runner.py", "lines": "12-14"}
      ],
      "issue": "...",
      "why_it_matters": "...",
      "recommendation": "...",
      "verification": "..."
    }
  ],
  "bundle_notes": ""
}
```

Severity values: `critical | high | medium | low` per `grading-rubric.md`.

## Bundling rule

Bundle structural patterns: 2+ sites sharing the same violation → one story per violation type. Example: 5 `application/` modules importing from `infrastructure/` directly become ONE `ARCH` story listing all 5 sites.

## Examples of good findings

- "ARCH-#: `application/training/runner.py:12-14` and `application/inference/runner.py:8-10` import `infrastructure.bigquery.client` directly, bypassing the port. Either define a port in `application/<pkg>/ports.py` and inject the adapter, or move the adapter call behind an existing port. Severity high because each direct import couples application code to a specific data source."
- "CFG-#: `core/training/types.py` defines `TrainingYAMLConfig` with `from_yaml()` and `to_yaml()` methods. Per AGENTS.md, YAML-shape dataclasses live in `application/`, not `core/`. Move to `application/training/config.py` and keep only the algorithm-input dataclass (e.g., `TrainingParams`) in core."

## Examples of bad findings (avoid)

- "ARCH: package has too many files" — no concrete violation
- "CFG: unclear what this config does" — not a defect; might be a DOC story

## Self-check before emitting

- Every architectural violation cites the import or relationship that's wrong.
- CFG findings reference AGENTS.md rules where applicable.
- Findings are ordered by severity desc.
