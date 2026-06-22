---
branch: chore/code-review-update
initial_review_commit: 23c29e1
last_updated_commit: 63faed4
last_staleness_scan:
  commit: 63faed4
  date: 2026-06-21
---

# Surface

## DX — Developer experience

**Grade:** B

DX-001 is verified. All Justfile recipes are documented; `just validate` runs the full gate. One new medium DX finding: DX-002 — the README Quick Start's first-run example uses `acheron submit`, which is not a real subcommand; the canonical onboarding path fails.

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

### DX-002 — README Quick Start command `acheron submit` no longer exists; the canonical first-run example fails

```yaml
status: open
severity: medium
effort: S
reviewed_at: 63faed4
last_verified_at:
  commit: 63faed4
  date: 2026-06-21
fixed_in: []
files:
  - path: README.md
    lines: 16
  - path: README.md
    lines: 169-184
  - path: src/acheron/cli.py
    lines: 138-150
related: [DOC-003]
```

**Issue.** The README Quick Start (README.md:16) walks the user through a first submission with a command that does not exist. The CLI exposes `submit` only via `submit-job` (cli.py:138-150), but the README shows `acheron submit` — a typo or stale alias. Running the Quick Start verbatim produces a "no such command" error.

**Why it matters.** The Quick Start is the primary onboarding path. A first-run user who copies the documented command gets a confusing error, and the surrounding context does not suggest the correct subcommand name. The fix is trivial but the docs are the entry point.

**Recommendation.** Replace `acheron submit` with the real subcommand in README.md:16. Verify all other CLI invocations in README.md against `cli.py` to surface any other drift.

**Verification.** `grep -n '^\s*acheron' README.md` and verify each command against `python -m acheron.cli --help` output.

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

**Grade:** B

DOC-001 and DOC-002 remain verified. One new medium DOC finding: DOC-003 — configuration docs drift across README, `.env.example`, and an undocumented dashboard env var (`ACHERON_TRUST_REVERSE_PROXY`).

### DOC-001 — Impl-phase and stale-prone comments violate AGENTS.md comment discipline

```yaml
status: verified
severity: low
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: be7b3ab
  date: 2026-06-20
fixed_in:
  - 92ed9da
files:
  - path: src/acheron/shell/orchestrator.py
    lines: 87-88
  - path: scripts/generate_dev_certs.py
    lines: 143-144
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

### DOC-003 — Configuration docs drift across README, .env.example, and an undocumented dashboard env var

```yaml
status: open
severity: medium
effort: S
reviewed_at: 63faed4
last_verified_at:
  commit: 63faed4
  date: 2026-06-21
fixed_in: []
files:
  - path: .env.example
    lines: 6
  - path: README.md
    lines: 136-148
  - path: dashboard/app.py
    lines: 58
  - path: src/acheron/shell/api/deps.py
    lines: 22-50
  - path: src/acheron/shell/orchestrator.py
    lines: 173-192
related: [DX-002]
```

**Issue.** The Layer 10 registration-token affordances shipped across three places that drifted: (1) `.env.example:6` documents an old `ACHERON_REGISTRATION_TOKEN` line that pre-dates the auto-generation feature; (2) README.md:136-148 still tells the user to set `ACHERON_REGISTRATION_TOKEN` manually; (3) `dashboard/app.py:58` reads `ACHERON_TRUST_REVERSE_PROXY` (verified in the new forward-auth test at ba0227b) but the variable is not documented in README or `.env.example`. The `ACHERON_OPEN_REGISTRATION` env var is documented in `acheron.yaml.example` and the README but is read directly in `deps.py:33` outside the new `Settings` loader (see CFG-003).

**Why it matters.** Configuration docs are the contract between operators and the orchestrator. Drift here means: a user who follows the docs will set a manually-typed token (now unnecessary) and not set the dashboard env var (now required for reverse-proxy auth), and a user who follows the docs to enable open registration will set the right env var but the orchestrator will read it the wrong way.

**Recommendation.** Update `.env.example` to remove the manual `ACHERON_REGISTRATION_TOKEN` line (or mark it as legacy/explicit-override) and add `ACHERON_TRUST_REVERSE_PROXY` with a one-line comment. Update README.md:136-148 to describe the auto-generated token and the dashboard proxy env var. Align the `ACHERON_OPEN_REGISTRATION` documentation with the loader (CFG-003).

**Verification.** `grep -rn 'ACHERON_' README.md .env.example acheron.yaml.example` matches every env var that is actually read by `src/`. New `ACHERON_*` env vars introduced in this diff appear in the docs; removed env vars do not.
