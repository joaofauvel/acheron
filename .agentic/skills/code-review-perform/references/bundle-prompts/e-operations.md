# Bundle E — Operations

## Themes
PERF, OBS, SEC

## Threshold

- PERF: medium
- OBS: medium
- SEC: low (anchored to `.agentic-rules/python/python-security-patterns-rules_v1.md`)

## Lens

- **PERF**: hot paths, complexity, BigQuery cost, memory footprint, vectorization opportunities, N+1 patterns in dbt or Python, redundant recomputation
- **OBS**: logging consistency, structured logs, metric recording, error reporting, training-run tracking, traceability of failures
- **SEC**: input validation, secrets handling, dependency hygiene, SQL/template injection in dbt macros, deserialization risks, anchored to `.agentic-rules/python/python-security-patterns-rules_v1.md`

## Lens questions

1. Are there loops over rows that could be vectorized (polars/pandas)?
2. Are dbt models doing full-table scans where partition pruning would apply?
3. Are there repeated identical queries within a single run?
4. Are logs consistent in format (structured vs free-form)?
5. Is there error reporting that surfaces to a tracker, or are errors only logged?
6. Are secrets read from env or vault, never hardcoded or logged?
7. Are user-influenced strings interpolated into SQL (dbt macros) without parameterization?
8. Are pickle/yaml/json loads on untrusted input (deserialization risk)?
9. Do Python deps have known vulnerabilities (cite by package name; consolidator can run `pip-audit` if available)?

## Files to read

- `src/forecastest/**/*.py`
- `macros/**/*.sql` (dbt macros — at repo root, not under dbt/)
- `models/**/*.sql` (dbt models for perf focus)
- `pyproject.toml` (deps)
- `.env.local.example` (secret-handling pattern)
- `.agentic-rules/python/python-security-patterns-rules_v1.md` (yardstick — read for criteria, do not file findings against it)

## Output

Same JSON shape as other bundles. SEC findings should reference the relevant `.agentic-rules` rule when applicable.

Severity values: `critical | high | medium | low` per `grading-rubric.md`.

## Bundling rule

Bundle PERF findings by pattern (e.g., "5 row-by-row loops in core/severity/" → one story). Bundle SEC by rule violated. 2+ sites sharing the same defect → one story.

## Examples of good findings

- "PERF-#: `core/severity/predict.py:_compute_severity` iterates rows in a Python loop (lines 45-78) over polars DataFrames. Vectorize using `pl.struct(...).map_elements(...)` or rewrite as `pl.when().then()` chains. Estimated 10-50× speedup on partition sizes >100k. Severity medium."
- "SEC-#: `macros/build_filter.sql` interpolates `var('user_segment')` into a SQL string without quoting. Per `.agentic-rules/python/python-security-patterns-rules_v1.md` SQL-injection rule, wrap the value with `adapter.quote()` or escape single quotes explicitly via `{{ value | replace(\"'\", \"''\") }}`. Severity medium — `var()` values are author-controlled so the exploit surface is small, but the anti-pattern propagates."
- "OBS-#: `application/training/runner.py:fit` uses `print()` for status messages (lines 23, 45, 78) instead of the `logging` module. Replace with structured logger calls so output integrates with downstream log aggregation. Severity medium."

## Self-check before emitting

- PERF findings cite the hot path and the proposed alternative.
- SEC findings cite the relevant rule (file + section if possible).
- OBS findings name the convention they violate.
- Do not file SEC findings against `.agentic-rules/` itself.
