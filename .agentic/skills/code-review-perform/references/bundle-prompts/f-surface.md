# Bundle F — Surface

## Themes
DX, PKG, DOC (high threshold — surface only patterns or significant gaps)

## Lens

- **DX**: `just` commands, error messages, build feedback loops, local setup friction, log clarity in dev
- **PKG**: `pyproject.toml` hygiene, dependency pins, version coherence, build scripts, install reliability
- **DOC**: docstrings (per AGENTS.md hard rules: 1-line module docstrings, Google style for functions, conciseness), READMEs, comment discipline (timeless/objective/concise per project rules)

## Threshold

High — only patterns or significant gaps. Do NOT file:
- Single missing docstring on a private function
- A single dependency that could be slightly newer
- A `just` command that could be slightly nicer

## Lens questions

1. Are `just` recipes documented in their default `--list` output? (Comments above the recipe.)
2. Does `just setup` work cleanly on a fresh checkout per docs?
3. Are dependency groups in `pyproject.toml` coherent (e.g., dev tools not in `[project.dependencies]`)?
4. Are key abstractions documented (Protocols, public ports, core algorithm entry points)?
5. Are there comments that violate AGENTS.md (impl-detail comments, phase references, stale code-coupled commentary)?
6. Is the README sufficient for a new dev to find the entry points?

## Files to read

- `Justfile`
- `pyproject.toml`
- `README.md`
- `.env.local.example`
- `src/forecastest/**/__init__.py` (module docstrings)
- `src/forecastest/**/ports.py` (port docstrings)
- A sample of `core/` and `application/` modules for comment audit

## Output

Same JSON shape as other bundles.

Severity values: `critical | high | medium | low` per `grading-rubric.md`.

## Bundling rule

Aggressively bundle. "Public ports lack docstrings" → one DOC story listing the ports, not one per port. 2+ sites → one story.

## Examples of good findings

- "DOC-#: 8 `ports.py` files across `application/` and `core/` lack module-level docstrings explaining the boundary the port crosses. Per AGENTS.md, ports describe roles; a 1-line docstring per file would clarify the consumer-vs-call-out boundary. List: [...]"
- "PKG-#: `pyproject.toml` `[project.dependencies]` includes `pytest` and `ruff` (lines 25, 28); these belong under `[tool.poetry.group.dev.dependencies]` to avoid shipping dev tooling with the runtime install."
- "DX-#: `just lint-strict` recipe is undocumented (no comment above it in `Justfile`); the strict variant differs from `just lint-python` in non-obvious ways."

## Examples of bad findings (avoid)

- "DOC: missing docstring on `_internal_helper`" — high threshold means private/internal don't count
- "PKG: could pin polars more tightly" — single dep, no concrete failure mode

## Self-check before emitting

- Patterns are bundled, not per-instance.
- DOC findings respect AGENTS.md ("avoid stale-prone comments" — don't recommend MORE comments unless real value).
- DX findings name the friction point and the missing affordance.
