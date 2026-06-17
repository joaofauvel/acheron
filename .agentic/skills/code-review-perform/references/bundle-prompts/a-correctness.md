# Bundle A — Correctness

## Themes
CORR, ML, MATH (all low-threshold per `themes-taxonomy.md`)

## Lens

You are reviewing the codebase through the lens of correctness — does the code do what it claims, are the ML practices sound, are the numerical computations stable? File findings under whichever prefix fits best:

- **CORR**: general logic bugs (off-by-one, wrong branching, incorrect defaults, wrong return types contradicting docstrings)
- **ML**: target leakage, train/test leakage, incorrect train-time vs inference-time behavior, biased sampling, OOF prediction integrity, calibration correctness, evaluation metric correctness
- **MATH**: numerical stability, divide-by-zero risk, NaN/Inf propagation, overflow/underflow, incorrect aggregation (e.g., averaging an average), precision loss in float comparisons

## Threshold

Low — catch every issue, even minor. The cost of missing a correctness bug is high.

## Lens questions

For each module read, ask:

1. Does the function do what its name and docstring claim?
2. Is there any path where wrong values are returned silently (None, NaN, default-init zeros)?
3. Is training data ever contaminated by future information (target leakage, look-ahead bias)?
4. Are train and inference paths consistent on the same input?
5. Are statistical operations (mean, weighted average, ratio) computed correctly across edge cases (empty input, single sample, all-zero weights)?
6. Could float arithmetic accumulate error that affects the result (e.g., summing many small numbers)?
7. Are sentinel values (`-1`, `0`, `NaN`) handled distinctly from valid data?

## Files to read (path globs)

- `src/forecastest/core/**/*.py`
- `src/forecastest/application/**/*.py`
- `src/forecastest/infrastructure/**/*.py` (smaller weight — adapters less likely to have ML/MATH bugs)
- `models/**/*.sql` (dbt models — the project lives at the repo root, not under dbt/)

Skip: `tests/**` (tests are reviewed by Bundle D).

## Output

Emit findings as a JSON array. Wrap in a single object:

```json
{
  "bundle": "a-correctness",
  "findings": [
    {
      "prefix": "CORR",
      "title": "Bare exception swallows training failures",
      "severity": "critical",
      "effort": "S",
      "files": [
        {"path": "src/forecastest/core/training/loop.py", "lines": "130-138"}
      ],
      "issue": "...",
      "why_it_matters": "...",
      "recommendation": "...",
      "verification": "..."
    }
  ],
  "bundle_notes": "Optional: any cross-cutting observations or files you couldn't fully analyze."
}
```

Severity values: `critical | high | medium | low` per `grading-rubric.md`.

## Bundling rule

When 2+ sites share the same defect (e.g., five places where `df.mean()` is called on potentially-empty frames), file ONE story listing all sites with the prefix that fits best. Do not file one story per site.

## Examples of good findings

**Good (specific, actionable, severity-justified):**
- "ML-#: training_data builder includes `order_business_revenue_basis` from the target month, leaking the prediction quantity into features. See src/forecastest/core/features/builder.py:45-62. Severity critical because it inflates apparent calibration on backtests."

**Good (bundled small issues):**
- "MATH-#: 4 sites compute weighted mean with denominator-zero risk: `core/training/calibration.py:88, core/severity/predict.py:34, application/backtest/aggregate.py:120, application/backtest/aggregate.py:180`. Severity medium because failures would be silent NaN propagation."

## Examples of bad findings (avoid)

- "CORR: function should be more robust" — vague, no file/lines, not actionable
- "MATH: float comparison uses `==`" — file 5 instances as ONE story, not 5
- "ML: consider adding more validation" — not specific to a known defect

## Self-check before emitting

- Every finding has a concrete file path with line range.
- Every severity has a one-sentence justification visible in `why_it_matters`.
- No finding overlaps a Bundle B/C/D/E/F lens (the consolidator will dedup, but minimize overlap).
- Findings are ordered by severity desc.
