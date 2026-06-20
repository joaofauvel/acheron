---
branch: chore/code-review-update
initial_review_commit: 23c29e1
last_updated_commit: d0b739b
last_staleness_scan:
  commit: d0b739b
  date: 2026-06-20
---

# Surface

## DX — Developer experience

**Grade:** A

DX-001 is verified (auto-generated dev certs via certs-init compose service at c8879ec). All Justfile recipes are documented; `just validate` runs the full gate. No new DX findings.

### DX-001 — Quick Start omits `just certs` — fresh clone breaks `docker compose up`

```yaml
status: verified
severity: medium
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: d0b739b
  date: 2026-06-20
fixed_in:
  - c8879eccc762329087e93db47440038b44529094
files:
  - path: README.md
    lines: 12-13
  - path: .gitignore
    lines: 20
  - path: docker-compose.yml
    lines: 25-27
related: []
```

**Issue.** The README Quick Start (README.md:12-13) instructed `cp .env.example .env && docker compose up --build`, but `certs/` was gitignored (.gitignore:20) so a fresh clone had no certs. docker-compose.yml:25-27 set `ACHERON_TLS_CERT_FILE: /certs/orchestrator.crt` for the orchestrator and mounted `./certs:/certs:ro` into every service. With no certs, Docker bind-mounted an empty dir, uvicorn tried to load a non-existent cert, and the orchestrator crashed on startup.

**Why it matters.** The documented first-run experience failed completely on a fresh clone, forcing a new developer to debug a TLS load error and read past the Quick Start to find the missing step. Medium because it broke the canonical onboarding path.

**Recommendation.** Add `just certs` to the Quick Start, or auto-generate certs on first `docker compose up` via a one-shot service.

**Verification.** Fresh clone → run the updated Quick Start verbatim → `docker compose ps` shows all services healthy.

## PKG — Packaging

**Grade:** A

PKG-001 is verified (jinja2 moved to optional-dependencies[dashboard] at b603046). All dev tools are in `[dependency-groups] dev`; deps are pinned with `~=`.

### PKG-001 — `jinja2` is a runtime dep of the acheron wheel but is only used by the separate dashboard package

```yaml
status: verified
severity: low
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: d0b739b
  date: 2026-06-20
fixed_in:
  - b60304617af08a56ebb95d72f0746e26bb18b12b
files:
  - path: pyproject.toml
    lines: 16
  - path: dashboard/app.py
    lines: 11
related: []
```

**Issue.** `jinja2~=3.1` was declared in `[project.dependencies]` (pyproject.toml:16), making it a runtime dependency of the acheron wheel. However, no module under `src/acheron/` imports jinja2; the sole consumer was `dashboard/app.py:11` (`Jinja2Templates`), which is a separate package not included in the wheel. The dashboard Docker stage got jinja2 incidentally because it `pip install`ed the acheron wheel for its FastAPI dep.

**Why it matters.** The acheron wheel's dependency manifest was misleading — it advertised jinja2 as a runtime need when the package never imports it. Anyone installing the wheel for orchestrator-only use pulled an unused dep, and the dashboard's real deps were coupled into acheron's manifest. Low because installation succeeded today; it's a hygiene/coupling issue.

**Recommendation.** Move `jinja2` to a dashboard-specific dependency manifest (as an extras group like `acheron[dashboard]`) or give dashboard/ its own pyproject.

**Verification.** `pip install acheron && python -c 'import acheron'` should not require jinja2; dashboard deps should be declared where the dashboard lives.

## DOC — Documentation

**Grade:** A

All stories verified. No open findings.

### DOC-001 — Impl-phase and stale-prone comments violate AGENTS.md comment discipline

```yaml
status: verified
severity: low
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: pending
  date: 2026-06-20
fixed_in: ["pending"]
files:
  - path: src/acheron/shell/orchestrator.py
    lines: 88-89
  - path: scripts/generate_dev_certs.py
    lines: 144
related: []
```

**Issue.** Two comments reference impl phases or internal symbols, violating AGENTS.md ("Comments should be timeless... Avoid stale-prone comments that reference impl details"). The third original site was removed when the cert key permission comment was dropped in the SEC-001 fix. Remaining: (1) orchestrator.py:88-89 — "Called from ``start()`` so the store methods (now ``async def``) can be awaited."; the "(now ``async def``)" references a past refactor. (2) generate_dev_certs.py:144 — "permissions on the files themselves are set in _write_pem_{cert,key}."; the comment couples to internal function names that could be renamed.

**Why it matters.** These comments will go stale as the code evolves (refactors, renames), exactly the decay AGENTS.md targets. Low because there is no functional impact; it's pure comment hygiene.

**Recommendation.** Rewrite each to state the invariant without phase or symbol references: e.g. (1) drop "(now ``async def``)"; (2) state the dir permission rationale without naming the writer functions.

**Verification.** `just lint-strict` passes; grep the two sites to confirm no "now", or function-name references remain in the comments.

### DOC-002 — README architecture tree references removed BatchAsync strategy

```yaml
status: verified
severity: medium
effort: S
reviewed_at: a1b11b2
last_verified_at:
  commit: d0b739b
  date: 2026-06-20
fixed_in:
  - 51a3ffab2c6d393b151e11776b8b44702f52d4af
files:
  - path: README.md
    lines: 67
related: []
```

**Issue.** The README architecture tree (README.md:60-74) still named `BatchAsync` as an execution strategy after it was removed from src/, tests/, stubs/, and dashboard/. The README was the only place in user-facing docs that still named the deleted class.

**Why it matters.** The architecture tree is the primary onboarding map for the package; a new dev trying to locate `BatchAsyncExecutor` would not find it and would be left unsure whether the doc or the code was wrong. This is exactly the kind of staleness AGENTS.md targets.

**Recommendation.** Drop `BatchAsync, and` from README.md:67 so the line reads `Sequential, Async, and Streaming execution strategies (Streaming is the default)`.

**Verification.** `grep -rn 'BatchAsync\|batch_async' README.md Justfile src/ tests/ dashboard/ stubs/ proto/ scripts/` returns zero hits in user-facing paths; `just validate` passes.
