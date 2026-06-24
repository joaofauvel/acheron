# Code Review Tackle — Bundle Plan

**Source**: `docs/code_review/summary.md` quick-wins list (42 stories at `last_updated_commit: eb6849c`).
**Scope**: define the order and grouping of `code-review-tackle` runs over the 42 quick wins.
**Out of scope**: refreshing `docs/code_review/` against post-fix code (use `code-review-update`), implementing the fixes themselves.

## Grouping rule

**By concern, severity-first, then effort-within-tier.** A "bundle" is the set of stories tackled in a single `code-review-tackle` run (typically one PR). Stories that attack the same code from different angles (e.g., `ARCH-017` + `DOC-005` on `shell/tls.py`) MUST land in the same commit, so they share a bundle.

## Bundle order

Tackle in the listed order. Earlier bundles unblock later ones in a few cases (called out per bundle).

### Bundle 1 — Registration token security
- **Severity**: 1 critical, 4 high · **Effort**: 5 S
- SEC-008, SEC-009, SEC-011, SEC-018, SEC-022
- All concern the auto-generated `ACHERON_REGISTRATION_TOKEN` lifecycle: plaintext log, file umask, dev-default fallback across 4 compose services.
- One PR. Resolves the only critical story + the dev-default bypass pattern.

### Bundle 2 — `translategemma-edge` operational break
- **Severity**: 1 high, 2 medium, 1 low · **Effort**: 4 S
- SEC-023, SEC-021, OBS-010, SEC-020
- The new edge service is broken-by-design (Dockerfile.edge omits the handler module). Fixing it before any deployer hits it.
- One PR.

### Bundle 3 — Greenfield rule violations
- **Severity**: 2 high, 2 medium · **Effort**: 4 S
- ARCH-017 + DOC-005 (delete `shell/tls.py` shim, migrate import sites, drop the shim docstring)
- ARCH-018 + MAINT-016 (drop `InvalidLanguagePathError` parent from `ChunkingTooLongForWorkerError`, retag consumers)
- Two PRs; the DOC/MAINT follow-ons MUST share a commit with their ARCH parent.

### Bundle 4 — RunPod forwarder & plan-time check
- **Severity**: 1 high, 1 medium · **Effort**: 2 S
- CORR-014, CORR-026
- Two unrelated 1-line correctness fixes (`shell/transports/http.py` + `core/planner.py`).
- One PR.

### Bundle 5 — HTTP worker correctness
- **Severity**: 2 medium, 3 low, 1 medium-M · **Effort**: 5 S + 1 M
- CORR-027, CORR-028, CORR-030, CORR-031, ARCH-022, ARCH-020
- `HttpWorker` edge cases. ARCH-020 (the leaky triple-magic-string refactor) subsumes CORR-027 + CORR-030; do them together.
- One PR. ARCH-020's M effort is the planning boundary — invoke `superpowers:writing-plans` for it.

### Bundle 6 — TLS rollout consolidation
- **Severity**: 3 medium, 1 medium-M, 1 low · **Effort**: 4 S + 1 M
- ARCH-021, TEST-015, TEST-017, PERF-007, PERF-008
- Uvicorn+TLS boilerplate + the `acheron.tls` helper's missing unit tests + the per-call `httpx.AsyncClient` consolidation.
- One PR. TEST-015's M effort requires `superpowers:writing-plans`.

### Bundle 7 — Worker package consolidation
- **Severity**: 1 medium, 2 low, 1 low-M · **Effort**: 3 S + 1 M
- MAINT-017, MAINT-018, MAINT-019, TYPE-010
- `parse_chunks_json(input)` helper + `Chunk` dataclass + `_ModelProto`/`_ProcessorProto` Protocol. Eliminates the qwen3tts/translategemma chunks.json parse duplication and the 3rd `Any`-typed self._model instance.
- One PR. TYPE-010's M effort requires `superpowers:writing-plans` (introduces a Protocol).

### Bundle 8 — Translatengemma handler tests & correctness
- **Severity**: 3 medium, 2 low · **Effort**: 1 S + 4 M
- TEST-014, TEST-016, CORR-029, CORR-032, CORR-033
- Handler tests (error path, partial-success, pad_token_id init, class-level mutation) + handler correctness (partial-success, in-memory materialization, tokenizer mutation).
- Depends on Bundle 7's helper + Protocol.
- One PR. Multiple M-effort stories → batch via `superpowers:dispatching-parallel-agents` after planning.

### Bundle 9 — Planner & config knobs
- **Severity**: 4 medium, 2 low · **Effort**: 6 S
- ARCH-019, CFG-009, CFG-010, CFG-011, DATA-009, OBS-011
- Fold `validate_chunking_fits_workers` into `compile_plan`; drop YAGNI `Settings.chars_per_token`; thread `WorkerSettings.model_id` and `WorkerCapabilities.max_input_tokens` through the YAMLs; add boundary tests; log the plan-time check.
- One PR.

### Bundle 10 — Documentation
- **Severity**: 1 low · **Effort**: 1 S
- DOC-006
- `submit_job` + `validate_chunking_fits_workers` `Raises:` sections.
- Trivial; mop up after the code changes are merged.

## Tackle mechanics

For each bundle:
1. Run from `.worktrees/code-review-tackle` (rebase onto `master` first if not already).
2. Per `code-review-tackle` skill: pre-flight staleness check → plan vs trivial decision → TDD for behavior changes → verification gate (`just lint-strict` + `just type-check` + `just test` + `dbt parse`) → correctness subagent pass → doc-staleness subagent pass → atomic commit.
3. Use `--pr` for each bundle (separate PR per bundle; no cross-bundle PRs).
4. One commit per story, per the skill's atomic-commit rule.

## Cross-cutting dependencies

- Bundle 7 must precede Bundle 8 (handler tests in Bundle 8 import the `parse_chunks_json` helper introduced in Bundle 7).
- Other bundles are independent and can be reordered without impact (the severity-first ordering is preferred for risk reduction but is not strictly required beyond the Bundle 7 → 8 chain).

## Stats

| Severity | Stories | Effort |
|---|---|---|
| Critical | 1 | 1 S |
| High | 8 | 8 S |
| Medium | 20 | 14 S + 6 M |
| Low | 13 | 13 S + 1 M |
| **Total** | **42** | **35 S + 7 M** |

10 bundles, mean 4.2 stories/bundle, range 1–6.

## Not in this plan

- The 97 non-quick-win stories (per the summary's per-theme grade counts: 138 open total – 42 quick wins = 96 remaining, plus 1 critical already counted = ~97 with mixed severity). Those are tackled in a second pass after the quick-win bundles complete, or as part of `code-review-update` once the code drifts.
- The `chore/code-review-update` branch is the source of the `summary.md`; this plan does not modify it.
