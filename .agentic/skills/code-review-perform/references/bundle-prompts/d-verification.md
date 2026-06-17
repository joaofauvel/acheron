# Bundle D — Verification

## Themes
TEST, REPRO, DATA

## Threshold

Medium for all three prefixes.

## Lens

- **TEST**: coverage of critical paths, fakes-vs-mocks discipline, fixture hygiene, AGENTS.md test independence rules ("Tests shouldn't use repo configuration files or depend on hardcoded project paths")
- **REPRO**: seed handling, deterministic ordering in training/backtest, frozen training-window invariants, version pinning of feature definitions, reproducible dbt builds
- **DATA**: dbt source freshness checks, schema tests presence (`unique`, `not_null`, `relationships`), contract tests on critical models, null/dupe handling, schema drift detection

## Lens questions

1. Are all `core/` modules covered by tests under `tests/core/<same-path>/`?
2. Are tests using `Mock` where a `Fake` (in-memory replacement) would be honest?
3. Do tests depend on hardcoded project paths or repo configuration files? (AGENTS.md hard rule)
4. Are random operations seeded explicitly (training, backtest, sampling)?
5. Are sort orders deterministic before reductions that could be order-sensitive?
6. Do dbt sources have freshness configurations?
7. Do critical dbt models have `unique` and `not_null` tests on key columns?
8. Are schema/contract tests present on outputs consumed by other layers?

## Files to read (path globs)

- `tests/**/*.py` (Python tests)
- `src/forecastest/**/*.py` (to check coverage gaps)
- `models/**/*.{sql,yml}` (dbt models — at repo root, not under dbt/)
- `tests/dbt/**` (dbt test SQL — sibling dir under tests/)
- `models/_sources.yml` (dbt sources config)

## Output

```json
{
  "bundle": "d-verification",
  "findings": [
    {
      "prefix": "TEST",
      "title": "core/severity has no unit tests",
      "severity": "high",
      "effort": "M",
      "files": [
        {"path": "src/forecastest/core/severity/", "lines": "all"}
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

Bundle by theme: "core/foo/ has zero unit tests" is one TEST story; "5 dbt models lack `not_null` on join keys" is one DATA story. 2+ sites sharing the same defect → one story.

## Examples of good findings

- "TEST-#: `core/severity/` has 4 modules and 0 unit tests under `tests/core/severity/`. Critical-path code (`predict.py`, `calibration.py`) lacks regression coverage. Severity high because future refactors are ungated."
- "REPRO-#: `core/training/sampling.py:_sample_partition` calls `random.choice` without explicit seeding; downstream OOF prediction order depends on this. Add seed param and propagate from training entry point. Severity medium — non-deterministic OOF order is hard to audit in retrospect."
- "DATA-#: `models/silver/feature_*.sql` (12 models) lack `not_null` tests on `partition_id` join key. Add to `_schema.yml`. Severity medium — silent join failures degrade feature population."

## Self-check before emitting

- Coverage gaps cite specific package paths.
- REPRO findings cite the unseeded operation and its downstream impact.
- DATA findings name the missing test type and the affected model count.
