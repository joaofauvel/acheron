---
branch: chore/code-review-update
initial_review_commit: 23c29e1
last_updated_commit: a1b11b2
last_staleness_scan:
  commit: a1b11b2
  date: 2026-06-19
---

# Surface

## DX — Developer experience

**Grade:** A

One medium finding: the README Quick Start omits `just certs`, so a fresh clone breaks on `docker compose up` because `certs/` is gitignored and the compose file mounts TLS certs into every service. All Justfile recipes are documented in `--list` output; `just validate` runs the full gate (lint-strict + lint-imports + type-check + type-check-pyright + test); error messages are actionable (TLS pair-mismatch, CLI TLS/connect errors, data-dir unwritable all tell the user how to fix it).

### DX-001 — Quick Start omits `just certs` — fresh clone breaks `docker compose up`

```yaml
status: open
severity: medium
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: a1b11b2
  date: 2026-06-19
fixed_in: []
files:
  - path: README.md
    lines: 12-13
  - path: .gitignore
    lines: 20
  - path: docker-compose.yml
    lines: 25-27
related: []
```

**Issue.** The README Quick Start (README.md:12-13) instructs `cp .env.example .env && docker compose up --build`, but `certs/` is gitignored (.gitignore:20) so a fresh clone has no certs. docker-compose.yml:25-27 sets `ACHERON_TLS_CERT_FILE: /certs/orchestrator.crt` (and key) for the orchestrator and mounts `./certs:/certs:ro` into every service. With no certs, Docker bind-mounts an empty dir, uvicorn tries to load a non-existent cert, and the orchestrator crashes on startup — its healthcheck fails, so dependent services (dashboard, all stubs) never start. The TLS section (README.md:124) mentions `just certs`, but the Quick Start — the first path a new dev follows — does not.

**Why it matters.** The documented first-run experience fails completely on a fresh clone, forcing a new developer to debug a TLS load error and read past the Quick Start to find the missing step. Medium because it breaks the canonical onboarding path but is a trivial doc fix with a workaround (the TLS section explains it).

**Recommendation.** Add `just certs` (or `uv run python scripts/generate_dev_certs.py`) to the Quick Start block between `cp .env.example .env` and `docker compose up --build`, with a one-line note that it generates the dev TLS certs the compose file mounts.

**Verification.** Fresh clone → run the updated Quick Start verbatim → `docker compose ps` shows all services healthy; without the certs step, `docker compose logs orchestrator` shows an SSL cert load failure.

## PKG — Packaging

**Grade:** A

One low finding: `jinja2` is declared as a runtime dependency of the acheron wheel but is only imported by the separate dashboard package (not included in the wheel). All dev tools are correctly in `[dependency-groups] dev`; all deps are pinned with `~=`; grpcio/grpcio-tools/grpcio-health-checking are all `~=1.81` (coherent); the wheel targets `src/acheron` only (dashboard/stubs are copied directly in Dockerfile runtime stages — intentional).

### PKG-001 — `jinja2` is a runtime dep of the acheron wheel but is only used by the separate dashboard package

```yaml
status: open
severity: low
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: a1b11b2
  date: 2026-06-19
fixed_in: []
files:
  - path: pyproject.toml
    lines: 16
  - path: dashboard/app.py
    lines: 11
related: []
```

**Issue.** `jinja2~=3.1` is declared in `[project.dependencies]` (pyproject.toml:16), making it a runtime dependency of the acheron wheel. However, no module under `src/acheron/` imports jinja2; the sole consumer is `dashboard/app.py:11` (`Jinja2Templates`), which is a separate package not included in the wheel (`packages = ["src/acheron"]`). The dashboard Docker stage gets jinja2 incidentally because it `pip install`s the acheron wheel for its FastAPI dep.

**Why it matters.** The acheron wheel's dependency manifest is misleading — it advertises jinja2 as a runtime need when the package never imports it. Anyone installing the wheel for orchestrator-only use pulls an unused dep, and the dashboard's real deps are coupled into acheron's manifest. Low because installation succeeds today; it's a hygiene/coupling issue, not a functional break.

**Recommendation.** Either move `jinja2` to a dashboard-specific dependency manifest (give `dashboard/` its own pyproject or an extras group like `acheron[dashboard]`), or document why it rides on the acheron wheel. Prefer decoupling the dashboard's deps from the acheron package.

**Verification.** `pip install acheron && python -c 'import acheron'` should not require jinja2; dashboard deps should be declared where the dashboard lives.

## DOC — Documentation

**Grade:** A

One medium finding: README architecture tree still names the removed BatchAsync strategy, the only user-facing doc to do so. One low finding on stale impl-phase comments elsewhere. PKG and DX prefixes are clean except for one each (jinja2 runtime dep used only by dashboard, Quick Start omits just certs step).

### DOC-001 — Impl-phase and stale-prone comments violate AGENTS.md comment discipline

```yaml
status: open
severity: low
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: a1b11b2
  date: 2026-06-19
fixed_in: []
files:
  - path: src/acheron/shell/orchestrator.py
    lines: 88-89
  - path: scripts/generate_dev_certs.py
    lines: 46-48
  - path: scripts/generate_dev_certs.py
    lines: 146-147
related: []
```

**Issue.** Three comments reference impl phases or internal symbols, violating AGENTS.md ("Comments should be timeless... Avoid stale-prone comments that reference impl details. Do NOT add unnecessary comments related to impl phases"). (1) orchestrator.py:151-152 — "Called from ``start()`` so the store methods (now ``async def``) can be awaited."; the "(now ``async def``)" references a past refactor. (2) generate_dev_certs.py:46-48 — "workers in docker-compose run as root today, but if a future non-root user is added..."; references current/future impl state. (3) generate_dev_certs.py:146-147 — "permissions on the files themselves are set in _write_pem_{cert,key}."; couples the comment to internal function names that could be renamed.

**Why it matters.** These comments will go stale as the code evolves (refactors, renames, permission changes), exactly the decay AGENTS.md targets. Low because there is no functional impact; it's pure comment hygiene.

**Recommendation.** Rewrite each to state the invariant without phase or symbol references: e.g. (1) drop "(now ``async def``)"; (2) "mode 0644 so any container user can read the cert"; (3) "dir world-executable so files are listable" without naming the writer functions.

**Verification.** `just lint-strict` passes; grep the three sites to confirm no "now", "today", "future", or function-name references remain in the comments.

### DOC-002 — README architecture tree references removed BatchAsync strategy

```yaml
status: fixed
severity: medium
effort: S
reviewed_at: a1b11b2
last_verified_at:
  commit: pending
  date: 2026-06-19
fixed_in:
  - pending
files:
  - path: README.md
    lines: 67
```

**Issue.** The diff deletes BatchAsyncExecutor (shell/executors/batch_async.py), its factory branch, ExecutorStrategy.BATCH_ASYNC, the StreamingWorker ABC and its three methods on GrpcWorker/HttpWorker, the BatchJob/BatchStatus models, PlanStep.batch, and the Redis (de)serialization of `batch`. The CLI default is flipped from `batch_async` to `streaming`. README.md:67 still reads `executors/      # Sequential, Async, BatchAsync, and Streaming execution strategies (Streaming is the default)`, naming a strategy that no longer exists in src/, tests/, stubs/, or dashboard/. The README is the only place in user-facing docs that still names the deleted class.

**Why it matters.** The architecture tree at README.md:60-74 is the primary onboarding map for the package; a new dev trying to locate `BatchAsyncExecutor` will not find it and will be left unsure whether the doc or the code is wrong. This is exactly the kind of staleness AGENTS.md targets (timeless, objective docs) and the greenfield rule (replace/refactor over legacy fallbacks) — the doc should follow the deletion rather than re-introducing the impression that `batch_async` is a valid strategy.

**Recommendation.** Drop `BatchAsync, and` from README.md:67 so the line reads `Sequential, Async, and Streaming execution strategies (Streaming is the default)`.

**Verification.** `grep -rn 'BatchAsync\|batch_async' README.md Justfile src/ tests/ dashboard/ stubs/ proto/ scripts/` returns zero hits in user-facing paths; `just validate` passes.

**Why it matters.** The architecture tree at README.md:60-74 is the primary onboarding map for the package; a new dev trying to locate `BatchAsyncExecutor` will not find it and will be left unsure whether the doc or the code is wrong. This is exactly the kind of staleness AGENTS.md targets (timeless, objective docs) and the greenfield rule (replace/refactor over legacy fallbacks) — the doc should follow the deletion rather than re-introducing the impression that `batch_async` is a valid strategy.

**Recommendation.** Drop `BatchAsync, and` from README.md:67 so the line reads `Sequential, Async, and Streaming execution strategies (Streaming is the default)`.

**Verification.** `grep -rn 'BatchAsync\|batch_async' README.md Justfile src/ tests/ dashboard/ stubs/ proto/ scripts/` returns zero hits in user-facing paths; `just validate` passes.
