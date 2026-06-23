---
branch: chore/code-review-update
initial_review_commit: 23c29e1
last_updated_commit: dbec2be
last_staleness_scan:
  commit: dbec2be
  date: 2026-06-23
---

# Surface

## DX — Developer experience

**Grade:** A

DX-001 is verified. One new DX finding: DX-003 (medium) — `just install` does not install the new `workers/qwen3tts/` workspace member, breaking the documented fresh-clone setup. DX-002 remains open (README Quick Start still uses the non-existent `acheron submit`).

### DX-001 — Quick Start omits `just certs` — fresh clone breaks `docker compose up`

```yaml
status: verified
severity: medium
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
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
  commit: dbec2be
  date: 2026-06-23
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

### DX-003 — `just install` does not install the new `workers/qwen3tts/` workspace member, breaking the documented fresh-clone setup

```yaml
status: open
severity: medium
effort: S
reviewed_at: dbec2be
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in: []
files:
  - path: Justfile
    lines: 38-40
  - path: pyproject.toml
    lines: 190-194
  - path: pyproject.toml
    lines: 136
related: []
```

**Issue.** The new `pyproject.toml` adds `[tool.uv.workspace] members = ["workers/qwen3tts"]` and `[tool.uv.sources] acheron = { workspace = true }`, but the `just install` recipe is still `uv sync --all-extras` (Justfile:40, unchanged from the prior review). uv does NOT pull workspace members into the venv on a plain `uv sync`; the `acheron-qwen3tts` package is only resolved with `--all-packages` (verified: `uv pip show acheron-qwen3tts` after `just install` returns 'package not found'; after `uv sync --all-extras --all-packages` it appears as an editable install). The `pyproject.toml:136` testpaths entry still collects `workers/qwen3tts/tests` because pytest's local `pythonpath = ["../.."]` from `workers/qwen3tts/pyproject.toml` adds the project root to sys.path, so `just test` still passes — masking the gap for the test path. A developer who runs the README's documented 'Without direnv' setup (README.md:43) gets an incomplete venv, and any script outside the project root that does `import workers.qwen3tts.handler` fails.

**Why it matters.** Fresh-clone onboarding is now broken: the canonical `just install` (and the README's `uv sync --all-extras` literal) does not match the workspace topology. Tests pass by accident through a pytest-relative path hack, so the gap stays invisible until someone tries to import a worker module from a script, a REPL, or a new package. This is the same shape of bug as DX-001 (Quick Start omits `just certs`) — the documented first-run path diverges from what the workspace actually requires.

**Recommendation.** Change `Justfile:40` from `uv sync --all-extras` to `uv sync --all-extras --all-packages`. Optionally update README.md:43 to match. A one-line fix that aligns the recipe with the new workspace member.

**Verification.** Fresh clone → `just install` → `uv run python -c "import workers.qwen3tts.handler; print(workers.qwen3tts.handler.__file__)"` prints a path under the worktree, not a ModuleNotFoundError. `just test` still passes (643+ tests, coverage ≥ 80%).

## PKG — Packaging

**Grade:** A

PKG-001 is verified. Two new PKG findings: PKG-002 (low) — `pyproject.toml` dead `root_package` key + duplicate `soundfile` dev entry; PKG-003 (medium) — `Dockerfile:39` (certs-init stage) pins `cryptography~=49.0` while `pyproject.toml:168` pins `cryptography~=46.0`.

### PKG-001 — `jinja2` is a runtime dep of the acheron wheel but is only used by the separate dashboard package

```yaml
status: verified
severity: low
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
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

### PKG-002 — `pyproject.toml` dead `root_package` key + duplicate `soundfile` dev entry — drift artifacts from the workspace scaffold merge

```yaml
status: open
severity: low
effort: S
reviewed_at: dbec2be
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in: []
files:
  - path: pyproject.toml
    lines: 143-145
  - path: pyproject.toml
    lines: 174
  - path: pyproject.toml
    lines: 181
related: []
```

**Issue.** Two pyproject.toml drift artifacts from the workspace-scaffold commit (cf8ee80) and the runpod pin commit (f92e74b): (a) `pyproject.toml:144` declares `root_package = "acheron"` (singular) AND `pyproject.toml:145` declares `root_packages = ["acheron", "workers"]` (plural). import-linter normalises by deleting the singular key when the plural is present (confirmed in `importlinter/application/use_cases.py:194-199`), so the line 144 entry is dead config. (b) `pyproject.toml:174` and `pyproject.toml:181` both declare `soundfile` in the dev group: `soundfile~=0.14` and `soundfile>=0.14.0`. Both pins are satisfied by any modern soundfile, so it's not a runtime bug, but the duplicate is dead config — the second form was almost certainly added to satisfy a different resolver without removing the first.

**Why it matters.** Dead config costs nothing today, but it costs a maintainer every time the file is touched. PKG-001 was the same shape (stale `jinja2` dep in the wrong group) and was cleaned up. The workspace expansion is the second-best moment to make `pyproject.toml` coherent — the next refactor will only add more drift.

**Recommendation.** Remove `root_package = "acheron"` from line 144 (keep `root_packages = ["acheron", "workers"]` as the single source of truth). Remove the duplicate `soundfile>=0.14.0` from line 181 (keep `soundfile~=0.14` to match the `~=` convention used everywhere else in the file).

**Verification.** `just lint-imports` still shows `Contracts: 3 kept, 0 broken`. `uv run python -c "import soundfile"` still imports cleanly. `grep -c soundfile pyproject.toml` returns 2 (one in the mypy override at line 112, one in the dev group at line 174).

### PKG-003 — `Dockerfile:39` (certs-init stage) pins `cryptography~=49.0` while `pyproject.toml:168` pins `cryptography~=46.0`

```yaml
status: open
severity: medium
effort: S
reviewed_at: dbec2be
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
fixed_in: []
files:
  - path: Dockerfile
    lines: 36-40
  - path: pyproject.toml
    lines: 168
related: [DOC-003]
```

**Issue.** f92e74b (commit message: 'use ACHERON_WORKER__ prefix + drop lazy runpod import') downgraded `cryptography` from `~=49.0` to `~=46.0` in the pyproject.toml dev group to match the runpod 1.9.x pin ('the highest line runpod 1.9.x supports: cryptography<47.0.0'). The same commit added `runpod~=1.9` to main deps. But the `Dockerfile:39` `certs-init` stage was NOT updated: it still installs `cryptography~=49.0` via pip. As a result, the orchestrator + dashboard + stub images all install cryptography 46.x at runtime (via the acheron wheel's transitive deps), but the one-shot certs-init image installs 49.x. A developer running `just validate` locally gets 46.0.7 (verified: `uv pip show cryptography | head -3`); a fresh `docker compose up` produces a certs-init container with 49.x. The two certs would be subtly different in serialisation (49.0 added several new OID arcs and changed `BestAvailableEncryption` defaults), which means locally-generated certs may not match the format of certs generated inside the dev compose.

**Why it matters.** The certs-init service is the canonical entry point for first-run dev certs (DX-001 closure). A version drift between local dev and the compose-orchestrated certs-init means `docker compose down && docker compose up` can produce different `.crt`/`.key` artifacts than `just certs` on the same host, breaking the 'certs are interchangeable between local and compose' invariant. The pin was downgraded for a real reason (runpod 1.9.x compatibility) but the rationale was not propagated to the certs-init image. This is the same shape of bug as DOC-003 (config docs drift across three sources).

**Recommendation.** Change `Dockerfile:39` from `pip install --no-cache-dir cryptography~=49.0` to `pip install --no-cache-dir cryptography~=46.0` to match the dev group. Or, better, accept the pin as a build-arg so the two stay in lockstep by construction: `ARG CRYPTOGRAPHY_VERSION=46.0 ... pip install --no-cache-dir cryptography~=${CRYPTOGRAPHY_VERSION}.0`.

**Verification.** `docker compose build certs-init && docker compose run --rm certs-init python -c 'import cryptography; print(cryptography.__version__)'` prints 46.x. `uv pip show cryptography | head -3` from the project root prints the same 46.x. The two byte-for-byte cert outputs match.

## DOC — Documentation

**Grade:** A

DOC-001 and DOC-002 remain verified. DOC-003 (medium) remains open and re-resolved: 1 of 4 sub-issues fixed (README Configuration table now describes auto-generation), 3 of 4 still open. No new DOC findings at high threshold.

### DOC-001 — Impl-phase and stale-prone comments violate AGENTS.md comment discipline

```yaml
status: verified
severity: low
effort: S
reviewed_at: 23c29e1
last_verified_at:
  commit: dbec2be
  date: 2026-06-23
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
  commit: dbec2be
  date: 2026-06-23
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
  commit: dbec2be
  date: 2026-06-23
fixed_in: []
files:
  - path: .env.example
    lines: 7
  - path: .env.example
    lines: 9-27
  - path: README.md
    lines: 139
  - path: dashboard/app.py
    lines: 58
  - path: src/acheron/shell/api/deps.py
    lines: 33
related: [DX-002, SEC-011, PKG-003]
```

**Issue.** The Layer 10 registration-token affordances shipped across three places that drifted, and the new dev-token default (SEC-011) widens the gap. (1) `.env.example:7` still ships `ACHERON_REGISTRATION_TOKEN=dev-registration-token` — a publicly known value per SEC-011; it should be replaced with an empty placeholder and a generation hint. (2) `README.md:139` (Configuration table) was rewritten in the prior review delta to describe the auto-generated token correctly — **FIXED**. (3) `.env.example:9-27` added a RunPod Serverless block but did NOT add `ACHERON_TRUST_REVERSE_PROXY`; `dashboard/app.py:58` still reads it (`os.environ.get('ACHERON_TRUST_REVERSE_PROXY') == '1'`) and it remains undocumented in both README and `.env.example`. (4) `ACHERON_OPEN_REGISTRATION` is still read directly in `deps.py:33` outside the new Settings loader — still deferred to CFG-003.

**Why it matters.** Configuration docs are the contract between operators and the orchestrator. Drift here means: a user who follows the docs will set the manual token (now unnecessary) and not set the dashboard env var (now required for reverse-proxy auth), and a user who follows the docs to enable open registration will set the right env var but the orchestrator will read it the wrong way. The dev-token default widens the blast radius from "operator confused" to "any attacker with the .env.example can register workers."

**Recommendation.** Update `.env.example:7` to empty the registration token and add an `ACHERON_TRUST_REVERSE_PROXY=0` line with a comment. Add `ACHERON_TRUST_REVERSE_PROXY` to the README's Configuration table. Align the `ACHERON_OPEN_REGISTRATION` documentation with the loader (CFG-003). Coordinate the empty-token change with the SEC-011 startup-validation fix.

**Verification.** `grep -rn 'ACHERON_' README.md .env.example acheron.yaml.example` matches every env var that is actually read by `src/`. New `ACHERON_*` env vars introduced in this diff appear in the docs; removed env vars do not. The registration-token example no longer contains `dev-registration-token`.
