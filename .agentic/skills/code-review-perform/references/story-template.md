# Story Template

Authoritative format for every story in `docs/code_review/`. Each story is an H3 inside a per-theme H2 section of a per-bundle file.

## Format

````markdown
### <PREFIX>-<NNN> — <Title>

```yaml
status: open                    # open | in-progress | fixed | verified | stale | wontfix
severity: high                  # critical | high | medium | low
effort: M                       # S | M | L
reviewed_at: <sha>              # frozen at story creation
last_verified_at:
  commit: <sha>
  date: 2026-05-08
fixed_in: []                    # appended on fix; "pending" placeholder until consolidator resolves
files:
  - path: src/forecastest/application/training/config.py
    lines: 12-78
related: []                     # cross-theme links inserted by consolidator (e.g., [CFG-002])
```

**Issue.** Concrete description of what's wrong, citing concrete code locations.

**Why it matters.** Concrete impact — what breaks or degrades because of this.

**Recommendation.** Concrete fix approach. Not "consider X"; either "do X" or "evaluate X vs Y".

**Verification.** How to verify the fix worked. Test commands, observable behavior change, or rubric check.
````

## Field semantics

| Field | Mutability | Notes |
|---|---|---|
| `status` | mutable | See lifecycle below |
| `severity` | mutable | Adjust during update if evidence changes |
| `effort` | mutable | Adjust as understanding sharpens |
| `reviewed_at` | **frozen at creation** | Anchors line numbers in time |
| `last_verified_at` | mutable | Updated by doc-staleness pass |
| `fixed_in` | append-only with one exception | `pending` placeholder may be resolved to SHA; resolved entries never rewritten |
| `files[].lines` | mutable | Re-resolved during staleness scans |
| `related` | mutable | Consolidator-managed cross-refs |

## Status lifecycle

States: `open`, `in-progress`, `fixed`, `verified`, `stale`, `wontfix`.

Transitions:

- `open → in-progress`: fix scheduled or started
- `in-progress → fixed`: code change committed (atomic commit with story-file update)
- `fixed → verified`: both subagent passes (correctness + doc-staleness) succeeded
- `open | in-progress → stale`: a later doc-staleness pass detected the cited issue no longer matches reality
- `stale → open | wontfix`: manual re-evaluation
- `open | in-progress | stale → wontfix`: explicit decision not to fix; rationale lives in story prose

State meanings:

- `open`: filed, no work started
- `in-progress`: fix in flight; prevents duplicate scheduling
- `fixed`: code change committed; awaiting subagent verification
- `verified`: both subagent passes succeeded
- `stale`: doc-staleness flagged drift; description no longer matches reality
- `wontfix`: explicit decision not to fix

`fixed`, `verified`, and `wontfix` are **immutable** for `update` flows and skipped by tackle's doc-staleness pass. Regressions become NEW stories with `related: [<old-id>]`, never re-open old ones.

## Examples

### EXC-001 — Bare exception swallows training failures

```yaml
status: open
severity: critical
effort: S
reviewed_at: abc1234
last_verified_at:
  commit: abc1234
  date: 2026-05-08
fixed_in: []
files:
  - path: src/forecastest/core/training/loop.py
    lines: 130-138
related: [CORR-002, TYPE-005]
```

**Issue.** `train_one_fold` wraps the entire fit call in `except Exception` and returns `None` on failure. Callers cannot distinguish "no training data" from "training crashed mid-fit."

**Why it matters.** A degenerate model can be returned and used downstream as if it were valid; integration tests pass with `None` short-circuited and silent failures reach inference.

**Recommendation.** Replace bare except with typed exception handling for `XGBoostError` and `polars.exceptions.ColumnNotFoundError`; let other exceptions propagate. Remove the `Optional[Model]` return; train failures should fail loud.

**Verification.** Add a test that injects a malformed training frame and asserts the typed exception propagates. `just test` passes; downstream consumers no longer have the `if model is None` branch.
