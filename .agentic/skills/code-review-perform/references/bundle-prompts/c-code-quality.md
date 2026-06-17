# Bundle C — Code quality

## Themes
MAINT, EXC, TYPE

## Threshold

- MAINT: high (patterns only, no nit-flooding)
- EXC: medium
- TYPE: medium

## Lens

- **MAINT**: file size, function length, naming clarity, dead code, brittleness/fragility hotspots, code-smell patterns (deep nesting, mutable default args, god objects)
- **EXC**: bare `except`, swallowed errors, exceptions used for control flow, missing exception types, exception messages that lack context
- **TYPE**: `Any` usage, missing annotations on public functions, `# type: ignore` audit, Protocol vs ABC drift, untyped imports without stubs

## Lens questions

1. Files >500 lines: does it have one clear responsibility, or is it doing too many things?
2. Functions >50 lines: can they be decomposed without inventing premature abstractions?
3. Bare `except` or `except Exception` — is the catch justified, or is it swallowing errors?
4. `# type: ignore` without justification comment.
5. `Any` type used where a concrete or generic type would fit.
6. Mutable default arguments (`def f(x=[])`)?
7. Dead code: imports, functions, classes never referenced (after accounting for entry points).
8. Naming: ambiguous abbreviations, names that conflict with project glossary.

## Files to read

- `src/forecastest/**/*.py`

Skip: `tests/**`, `models/**`, `macros/**` (dbt project is at the repo root, not under `dbt/`).

## Output

Same JSON shape as other bundles.

## Bundling rule

Aggressively bundle MAINT findings — file size and function length issues are pattern-level, not per-instance. Bundle TYPE findings by category (`Any` usage in N modules → one story; `# type: ignore` audit → one story).

## Examples of good findings

- "EXC-#: `core/training/loop.py:130`, `core/severity/predict.py:78`, `application/backtest/runner.py:45` all wrap entire fit/predict calls in `except Exception` and log+continue. Replace with typed exception handling for the specific recoverable errors; let other exceptions propagate. Severity medium because silent failures degrade output quality without alerting."
- "TYPE-#: 12 `# type: ignore` comments in `core/` lack justification comments. Audit list: [...]. Replace with minimal type stubs or refactor to remove the ignore. Severity medium because each one is a future-typing-debt accumulator."

## Examples of bad findings (avoid)

- "MAINT: this file is long" — show the file, lines of code, and what the responsibilities are; bundle if multiple files
- "EXC: should add error message" — not a defect unless the missing context causes real diagnostic problems

## Self-check before emitting

- MAINT findings are pattern-level, not per-line nit.
- EXC findings cite the swallowing site and what the recovery does (or fails to do).
- TYPE findings enumerate sites for bundling.
