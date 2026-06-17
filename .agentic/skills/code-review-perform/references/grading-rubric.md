# Grading Rubric

Mechanical, per-theme, deterministic. Same set of stories produces the same grade across runs.

## Severity definitions

| Severity | Definition | Examples |
|---|---|---|
| **critical** | Wrong outputs, data loss, security exposure, or silent failures that affect production behavior | Target leakage in training, secrets in logs, exception swallowed silently producing degenerate model |
| **high** | Significant correctness, fragility, or maintainability risk that will cause real problems in normal use | Missing tests for critical path, unstable abstraction with multiple consumers, brittle config that breaks on rename |
| **medium** | Notable issue worth fixing but not urgent | Inconsistent error handling pattern, recurring small smell, missing observability on important code path |
| **low** | Minor improvement, polish, or future-friendliness | Naming improvement, docstring gap, refactor opportunity |

## Grade computation per theme

For each theme prefix, count stories by severity considering only stories with `status: open | in-progress | stale`. Stories with `status: fixed | verified | wontfix` do NOT count toward grades.

Stale stories count as their last-known severity until re-evaluated.

| Grade | Criteria |
|---|---|
| **A** | 0 critical AND ≤2 high AND ≤2 medium |
| **B** | 0 critical AND ((3–5 high AND ≤8 medium) OR (≤2 high AND 3–8 medium)) |
| **C** | 1 critical OR 6–8 high OR 9–15 medium |
| **D** | 2 critical OR 9–12 high OR 16+ medium |
| **F** | 3+ critical OR 13+ high |

Algorithm: check each grade row in order F→A. Take the worst grade whose criteria match. Low-severity stories do not affect the grade directly; they appear in the per-theme narrative only.

## Examples

- Theme with 0 critical, 1 high, 2 medium → **A** (matches A row: ≤2 high AND ≤2 medium)
- Theme with 0 critical, 1 high, 5 medium → **B** (≤2 high AND 3–8 medium)
- Theme with 0 critical, 4 high, 5 medium → **B** (3–5 high AND ≤8 medium)
- Theme with 0 critical, 0 high, 10 medium → **C** (9–15 medium)
- Theme with 1 critical, 0 high, 0 medium → **C** (1 critical)
- Theme with 0 critical, 9 high, 0 medium → **D** (9–12 high)
- Theme with 3 critical, 0 high → **F** (3+ critical)

## No aggregate codebase grade

`summary.md` lists per-theme grades and a "Top concerns" section. There is no aggregate letter grade for the whole codebase. Different consumers weight themes differently; a single number misleads.

## Tuning

Thresholds are hardcoded in this file. If they need tuning, edit this file and re-run `code-review-update`. Do not add a config knob — the spec hard rule.
