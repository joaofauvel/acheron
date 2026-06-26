---
branch: code-review-refresh
initial_review_commit: 23c29e1
last_updated_commit: 77aadcd327643367129d4b3874a3c9c217b40084
last_staleness_scan:
  commit: 77aadcd327643367129d4b3874a3c9c217b40084
  date: 2026-06-26
---

# Surface

## DX — Developer experience

**Grade:** A

DX-001 is verified. DX-002 (medium) transitioned to `fixed` in 5b55e6f (README Quick Start replaced `acheron submit` with the canonical `acheron job ...` form). DX-003 (medium) remains open and re-resolved: the new `workers/granite_speech` workspace member widens the gap. No new DX findings at high threshold. **2026-06-26 refresh**: DX-004 added — `.envrc.example:5` uses `uv sync --all-extras` without `--all-packages`, so direnv-activated venvs also miss workspace members.

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
status: fixed
severity: medium
effort: S
reviewed_at: 63faed4
last_verified_at:
  commit: e54458416e9bfe890a473dd9d542978d205b40a1
  date: 2026-06-23
fixed_in:
  - 5b55e6f
files:
  - path: README.md
    lines: 16
  - path: README.md
    lines: 214-219
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
  commit: e54458416e9bfe890a473dd9d542978d205b40a1
  date: 2026-06-23
fixed_in: []
files:
  - path: Justfile
    lines: 38-40
  - path: pyproject.toml
    lines: 198-202
  - path: pyproject.toml
    lines: 140-144
related: []
```

**Issue.** The new `pyproject.toml` adds `[tool.uv.workspace] members = ["workers/qwen3tts"]` and `[tool.uv.sources] acheron = { workspace = true }`, but the `just install` recipe is still `uv sync --all-extras` (Justfile:40, unchanged from the prior review). uv does NOT pull workspace members into the venv on a plain `uv sync`; the `acheron-qwen3tts` package is only resolved with `--all-packages` (verified: `uv pip show acheron-qwen3tts` after `just install` returns 'package not found'; after `uv sync --all-extras --all-packages` it appears as an editable install). The `pyproject.toml:136` testpaths entry still collects `workers/qwen3tts/tests` because pytest's local `pythonpath = ["../.."]` from `workers/qwen3tts/pyproject.toml` adds the project root to sys.path, so `just test` still passes — masking the gap for the test path. A developer who runs the README's documented 'Without direnv' setup (README.md:43) gets an incomplete venv, and any script outside the project root that does `import workers.qwen3tts.handler` fails.

**Why it matters.** Fresh-clone onboarding is now broken: the canonical `just install` (and the README's `uv sync --all-extras` literal) does not match the workspace topology. Tests pass by accident through a pytest-relative path hack, so the gap stays invisible until someone tries to import a worker module from a script, a REPL, or a new package. This is the same shape of bug as DX-001 (Quick Start omits `just certs`) — the documented first-run path diverges from what the workspace actually requires.

**Recommendation.** Change `Justfile:40` from `uv sync --all-extras` to `uv sync --all-extras --all-packages`. Optionally update README.md:43 to match. A one-line fix that aligns the recipe with the new workspace member.

**Verification.** Fresh clone → `just install` → `uv run python -c "import workers.qwen3tts.handler; print(workers.qwen3tts.handler.__file__)"` prints a path under the worktree, not a ModuleNotFoundError. `just test` still passes (643+ tests, coverage ≥ 80%).

## PKG — Packaging

**Grade:** A

PKG-001 is verified. Two new PKG findings: PKG-002 (low) — `pyproject.toml` dead `root_package` key + duplicate `soundfile` dev entry; PKG-003 (medium) — `Dockerfile:39` (certs-init stage) pins `cryptography~=49.0` while `pyproject.toml:168` pins `cryptography~=46.0`. **2026-06-26 refresh**: PKG-004 added — All three worker packages duplicate `pythonpath = ["../.."]` in `[tool.pytest.ini_options]`, masking the DX-003 workspace install gap.

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
status: fixed
severity: low
effort: S
reviewed_at: dbec2be
last_verified_at:
  commit: pending
  date: 2026-06-23
fixed_in: ["pending"]
files:
  - path: pyproject.toml
    lines: 152-153
  - path: pyproject.toml
    lines: 182
  - path: pyproject.toml
    lines: 189
related: []
```

**Issue.** Two pyproject.toml drift artifacts from the workspace-scaffold commit (cf8ee80) and the runpod pin commit (f92e74b): (a) `pyproject.toml:144` declares `root_package = "acheron"` (singular) AND `pyproject.toml:145` declares `root_packages = ["acheron", "workers"]` (plural). import-linter normalises by deleting the singular key when the plural is present (confirmed in `importlinter/application/use_cases.py:194-199`), so the line 144 entry is dead config. (b) `pyproject.toml:174` and `pyproject.toml:181` both declare `soundfile` in the dev group: `soundfile~=0.14` and `soundfile>=0.14.0`. Both pins are satisfied by any modern soundfile, so it's not a runtime bug, but the duplicate is dead config — the second form was almost certainly added to satisfy a different resolver without removing the first.

**Why it matters.** Dead config costs nothing today, but it costs a maintainer every time the file is touched. PKG-001 was the same shape (stale `jinja2` dep in the wrong group) and was cleaned up. The workspace expansion is the second-best moment to make `pyproject.toml` coherent — the next refactor will only add more drift.

**Recommendation.** Remove `root_package = "acheron"` from line 144 (keep `root_packages = ["acheron", "workers"]` as the single source of truth). Remove the duplicate `soundfile>=0.14.0` from line 181 (keep `soundfile~=0.14` to match the `~=` convention used everywhere else in the file).

**Verification.** `just lint-imports` still shows `Contracts: 3 kept, 0 broken`. `uv run python -c "import soundfile"` still imports cleanly. `grep -c soundfile pyproject.toml` returns 2 (one in the mypy override at line 112, one in the dev group at line 174).

### PKG-003 — `Dockerfile:39` (certs-init stage) pins `cryptography~=49.0` while `pyproject.toml:168` pins `cryptography~=46.0`

```yaml
status: fixed
severity: medium
effort: S
reviewed_at: dbec2be
last_verified_at:
  commit: pending
  date: 2026-06-23
fixed_in: ["pending"]
files:
  - path: Dockerfile
    lines: 42-46
  - path: pyproject.toml
    lines: 176
related: [DOC-003]
```

**Issue.** f92e74b (commit message: 'use ACHERON_WORKER__ prefix + drop lazy runpod import') downgraded `cryptography` from `~=49.0` to `~=46.0` in the pyproject.toml dev group to match the runpod 1.9.x pin ('the highest line runpod 1.9.x supports: cryptography<47.0.0'). The same commit added `runpod~=1.9` to main deps. But the `Dockerfile:39` `certs-init` stage was NOT updated: it still installs `cryptography~=49.0` via pip. As a result, the orchestrator + dashboard + stub images all install cryptography 46.x at runtime (via the acheron wheel's transitive deps), but the one-shot certs-init image installs 49.x. A developer running `just validate` locally gets 46.0.7 (verified: `uv pip show cryptography | head -3`); a fresh `docker compose up` produces a certs-init container with 49.x. The two certs would be subtly different in serialisation (49.0 added several new OID arcs and changed `BestAvailableEncryption` defaults), which means locally-generated certs may not match the format of certs generated inside the dev compose.

**Why it matters.** The certs-init service is the canonical entry point for first-run dev certs (DX-001 closure). A version drift between local dev and the compose-orchestrated certs-init means `docker compose down && docker compose up` can produce different `.crt`/`.key` artifacts than `just certs` on the same host, breaking the 'certs are interchangeable between local and compose' invariant. The pin was downgraded for a real reason (runpod 1.9.x compatibility) but the rationale was not propagated to the certs-init image. This is the same shape of bug as DOC-003 (config docs drift across three sources).

**Recommendation.** Change `Dockerfile:39` from `pip install --no-cache-dir cryptography~=49.0` to `pip install --no-cache-dir cryptography~=46.0` to match the dev group. Or, better, accept the pin as a build-arg so the two stay in lockstep by construction: `ARG CRYPTOGRAPHY_VERSION=46.0 ... pip install --no-cache-dir cryptography~=${CRYPTOGRAPHY_VERSION}.0`.

**Verification.** `docker compose build certs-init && docker compose run --rm certs-init python -c 'import cryptography; print(cryptography.__version__)'` prints 46.x. `uv pip show cryptography | head -3` from the project root prints the same 46.x. The two byte-for-byte cert outputs match.

## DOC — Documentation

**Grade:** B

DOC-001 and DOC-002 remain verified. DOC-003 (medium) remains open and re-resolved: 1 of 4 sub-issues fixed (README Configuration table now describes auto-generation), 3 of 4 still open. DOC-004 (medium) widens with the new translategemma worker still absent from the README. Two new DOC findings in 8c: DOC-005 (medium) — `shell/tls.py` back-compat shim docstring violates the greenfield rule [related: ARCH-017]; DOC-006 (low) — `submit_job` and `validate_chunking_fits_workers` have incomplete Google-style `Raises:` sections. **2026-06-26 refresh**: DOC-004 marked stale (README has been rewritten to include all 3 workers); DOC-007 added — 24 source files have multi-line module docstrings that violate AGENTS.md's 1-line module-docstring rule.

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
  commit: 0e6c576
  date: '2026-06-24'
fixed_in: []
files:
  - path: .env.example
    lines: 4-9, 29
  - path: .env.example
    lines: 9-29
  - path: README.md
    lines: 182-198
  - path: dashboard/app.py
    lines: 58
  - path: src/acheron/shell/api/deps.py
    lines: 25-32
  - path: docker-compose.yml
    lines: 35-36
related:
- DX-002
- SEC-011
- PKG-003
```

**Issue.** The Layer 10 registration-token affordances shipped across three places that drifted, and the new dev-token default (SEC-011) widens the gap. (1) `.env.example:7` still ships `ACHERON_REGISTRATION_TOKEN=dev-registration-token` — a publicly known value per SEC-011; it should be replaced with an empty placeholder and a generation hint. (2) `README.md:139` (Configuration table) was rewritten in the prior review delta to describe the auto-generated token correctly — **FIXED**. (3) `.env.example:9-27` added a RunPod Serverless block but did NOT add `ACHERON_TRUST_REVERSE_PROXY`; `dashboard/app.py:58` still reads it (`os.environ.get('ACHERON_TRUST_REVERSE_PROXY') == '1'`) and it remains undocumented in both README and `.env.example`. (4) `ACHERON_OPEN_REGISTRATION` is still read directly in `deps.py:33` outside the new Settings loader — still deferred to CFG-003.

**Why it matters.** Configuration docs are the contract between operators and the orchestrator. Drift here means: a user who follows the docs will set the manual token (now unnecessary) and not set the dashboard env var (now required for reverse-proxy auth), and a user who follows the docs to enable open registration will set the right env var but the orchestrator will read it the wrong way. The dev-token default widens the blast radius from "operator confused" to "any attacker with the .env.example can register workers."

**Recommendation.** Update `.env.example:7` to empty the registration token and add an `ACHERON_TRUST_REVERSE_PROXY=0` line with a comment. Add `ACHERON_TRUST_REVERSE_PROXY` to the README's Configuration table. Align the `ACHERON_OPEN_REGISTRATION` documentation with the loader (CFG-003). Coordinate the empty-token change with the SEC-011 startup-validation fix.

**Verification.** `grep -rn 'ACHERON_' README.md .env.example acheron.yaml.example` matches every env var that is actually read by `src/`. New `ACHERON_*` env vars introduced in this diff appear in the docs; removed env vars do not. The registration-token example no longer contains `dev-registration-token`.

### DOC-004 — README architecture tree, CI section, and Test paths omit the new `granite_speech` worker

```yaml
status: stale
severity: medium
effort: S
reviewed_at: e54458416e9bfe890a473dd9d542978d205b40a1
last_verified_at:
  commit: e54458416e9bfe890a473dd9d542978d205b40a1
  date: 2026-06-23
fixed_in: []
files:
  - path: README.md
    lines: 87-92
  - path: README.md
    lines: 79
  - path: README.md
    lines: 162-168
related: [DOC-003]
```

**Issue.** The new `workers/granite_speech/` workspace member is wired into the workspace, has its own CI job, its own Dockerfile.runpod, its own tests, and its own `docker-compose.yml` service — but the README's onboarding map still names only the qwen3tts worker. (1) Architecture tree (README.md:76-77) lists `qwen3tts/` under `workers/`, with no `granite_speech/` entry. (2) Test paths (README.md:125) lists only `workers/qwen3tts/tests/`, omitting both `workers/granite_speech/tests/` and `workers/_shared/tests/`. (3) CI section (README.md:160-163) lists two published images — `acheron-qwen3tts-runpod` and `acheron-worker-edge` — but `.github/workflows/build-workers.yml:48-76` adds a third `build-granite-speech` job publishing `acheron-granite-speech-runpod:latest` and `:<sha>`, which is not in the README.

**Why it matters.** The README is the primary onboarding map for the package. The drift is exactly the shape AGENTS.md targets (the qwen3tts/granite_speech asymmetry would not be visible to a new contributor reading the README).

**Recommendation.** Update README.md:76-77 to add `granite_speech/  # Granite-Speech ASR RunPod + edge worker` under `workers/`. Update README.md:125 to add `workers/granite_speech/tests/`, `workers/_shared/tests/`. Update README.md:160-163 to add `acheron-granite-speech-runpod` to the CI bullet list.

**Verification.** `grep -n 'granite' README.md` returns hits in the architecture tree, test paths, and CI section.

## DOC (8c delta)

### DOC-005 — `shell/tls.py` shim docstring violates greenfield rule; references past move and old import path

```yaml
status: verified
severity: medium
effort: S
reviewed_at: eb6849c85d83f2277eb450f18a11e63cae2defd1
last_verified_at:
  commit: 2b8c721
  date: 2026-06-24
fixed_in: [2b8c721]
files:
  - path: src/acheron/shell/tls.py
    lines: 1-24
  - path: src/acheron/tls.py
    lines: 1-6
related: [ARCH-017]
```

**Issue.** The 24-line `src/acheron/shell/tls.py` is a back-compat shim that re-exports from the new top-level `src/acheron/tls.py`. AGENTS.md says: "Project is greenfield, it should never have legacy code or legacy fallbacks, replace/refactor old paths over adding compatibility fallbacks." The shim has 4 known internal callers — `src/acheron/shell/api/__main__.py:10`, `src/acheron/shell/health.py:18`, `src/acheron/shell/step_handler.py:12`, and `src/acheron/cli.py:20` — that should be updated to import from `acheron.tls` directly and the shim deleted. The docstring compounds the rule violation with stale-prone wording: "live in :mod:`acheron.tls` now" (the "now" is the AGENTS.md red flag), "the helpers were moved" (past-tense move), and "so existing import sites keep working" (references the old API path). All three references will go stale once the old path is gone (or stay stale if it isn't).

**Why it matters.** The greenfield rule is a hard rule in AGENTS.md. A back-compat shim is exactly the "compatibility fallback" the rule prohibits. The 4 callers that should be migrated are all in-tree, so the migration is mechanical. Leaving the shim in place also leaves a stale-prone docstring that violates the timeless-comment rule.

**Recommendation.** Update the 4 callers to `from acheron.tls import ...` and delete `src/acheron/shell/tls.py`. The new top-level `src/acheron/tls.py:1-6` docstring's second paragraph ("Lives at the top level... not under shell") is also borderline stale-prone structural rationale; trim to a 1-line docstring per AGENTS.md's module-docstring convention.

**Verification.** `grep -rn 'acheron.shell.tls' src/ tests/` returns zero hits; `just test` still passes; `grep -rn 'from acheron.shell.tls' .` returns nothing; `python -c 'import acheron.tls; print(acheron.tls.__file__)'` resolves to `src/acheron/tls.py`.

### DOC-006 — `submit_job` and `validate_chunking_fits_workers` have incomplete Google-style `Raises:` sections after the 8c plan-time check

```yaml
status: fixed
severity: low
effort: S
reviewed_at: eb6849c85d83f2277eb450f18a11e63cae2defd1
last_verified_at:
  commit: bc28597
  date: 2026-06-25
fixed_in:
- bc28597
files:
- path: src/acheron/shell/orchestrator.py
  lines: 253-265
- path: src/acheron/core/planner.py
  lines: 140-144
related:
- ARCH-019
- CFG-009
```

**Issue.** Two Google-style docstring gaps introduced by the 8c delta. (1) `Orchestrator.submit_job` docstring (orchestrator.py:216-224) says `Raises: AcheronError: If plan compilation fails (e.g. invalid language path).` — but the function now calls `validate_chunking_fits_workers` after `compile_plan` (orchestrator.py:244-249), which raises `ChunkingTooLongForWorkerError` (subclass of `InvalidLanguagePathError`/AcheronError). The `Raises:` section does not name the new exception class. (2) `validate_chunking_fits_workers` docstring (planner.py:92-111) is narrative and has no `Raises:` section at all, even though the function raises `ValueError` (planner.py:112-114) and `ChunkingTooLongForWorkerError` (planner.py:121-128). AGENTS.md: "For function and method docstrings use google style as per ruff config in `pyproject.toml`" — ruff pydocstyle convention is "google" (pyproject.toml:74), which expects `Raises:` to enumerate specific exception types.

**Why it matters.** Google-style docstrings are the documented convention. The missing `Raises:` entries mean a reader cannot tell from the signature+docstring which concrete exception will fire for a given failure mode (e.g. `ValueError` for `chars_per_token <= 0` is invisible to the reader). The new `ChunkingTooLongForWorkerError` is an Acheron subclass, so existing handling still works — but the docstring contract is now misleading: "AcheronError: If plan compilation fails" implies the new exception is a plan-compilation failure, when in fact it is a config-bounds failure raised AFTER successful plan compilation.

**Recommendation.** Rewrite orchestrator.py:219-223 to: `AcheronError: If plan compilation or input-budget validation fails (e.g. invalid language path, chunking exceeds worker max_input_tokens).` Or, more precise, list `InvalidLanguagePathError` and `ChunkingTooLongForWorkerError` separately. Add a `Raises:` section to planner.py:92-111 documenting `ValueError` (chars_per_token <= 0) and `ChunkingTooLongForWorkerError` (chunking_max_length exceeds a worker's max_input_tokens).

**Verification.** Read both docstrings; the `Raises:` sections enumerate every exception the function body can raise. `ruff check --select D .` does not flag them. `python -c 'import inspect; from acheron.core.planner import validate_chunking_fits_workers; print(inspect.getdoc(validate_chunking_fits_workers))'` shows the new section.

### DX-004 — `.envrc.example:5` uses `uv sync --all-extras` without `--all-packages`, so direnv-activated venvs also miss workspace members

```yaml
status: open
severity: medium
effort: S
reviewed_at: 77aadcd
last_verified_at:
  commit: 77aadcd
  date: 2026-06-26
fixed_in: []
files:
  - path: .envrc.example
    lines: 5
  - path: Justfile
    lines: 38-40
  - path: pyproject.toml
    lines: 198-202
related: [DX-003]
```

**Issue.** The same workspace-topology gap that DX-003 flagged in `Justfile:40` is also present in `.envrc.example:5`: `[[ -d ".venv" ]] || ( uv venv && uv sync --all-extras )`. A developer who follows the direnv path (the recommended workflow per README.md:14) copies `.envrc.example` to `.envrc`, direnv creates the venv with `uv sync --all-extras`, and the resulting venv does not include `acheron-qwen3tts`, `acheron-granite-speech`, or `acheron-translategemma` as editable installs. The README does not currently mention the `--all-packages` flag; the Justfile recipe is the only place where it could be standardised, and `.envrc.example` is the direnv counterpart. Same shape of bug as DX-001 (`just certs` omission) and DX-003 (`uv sync` flag): a documented first-run path diverges from what the workspace actually requires.

**Why it matters.** Two canonical onboarding paths (Justfile and direnv) both produce incomplete venvs. A developer who happens to use the direnv path (and skips the Justfile) will not see the `just install` DX-003 reminder either. Worker tests still pass by accident because each worker's `[tool.pytest.ini_options] pythonpath = ["../.."]` adds the project root to sys.path; the gap stays invisible until someone tries to import a worker module from a REPL, a script, or a new package. The fix is a one-line change to `.envrc.example:5` mirroring the Justfile fix.

**Recommendation.** Change `.envrc.example:5` from `[[ -d ".venv" ]] || ( uv venv && uv sync --all-extras )` to `[[ -d ".venv" ]] || ( uv venv && uv sync --all-extras --all-packages )`. Pair with the DX-003 fix to keep both entry points in lockstep. Optionally add a one-line comment in `.envrc.example` noting that `--all-packages` is required because Acheron's worker packages are uv workspace members, not transitive deps.

**Verification.** Fresh clone → `cp .envrc.example .envrc && direnv allow` (or `bash -c 'source .envrc'`) → `uv run python -c "import workers.qwen3tts.handler, workers.granite_speech.handler, workers.translategemma.handler"` resolves all three under the worktree, not ModuleNotFoundError. `just test` still passes. `just install` and the direnv path produce equivalent venvs.

### PKG-004 — All three worker packages duplicate `pythonpath = ["../.."]` in `[tool.pytest.ini_options]`, masking the DX-003 workspace install gap

```yaml
status: open
severity: low
effort: S
reviewed_at: 77aadcd
last_verified_at:
  commit: 77aadcd
  date: 2026-06-26
fixed_in: []
files:
  - path: workers/qwen3tts/pyproject.toml
    lines: 22
  - path: workers/granite_speech/pyproject.toml
    lines: 21
  - path: workers/translategemma/pyproject.toml
    lines: 21
  - path: workers/_shared/pyproject.toml
    lines: 2
related: [DX-003, DX-004]
```

**Issue.** `workers/qwen3tts/pyproject.toml:22`, `workers/granite_speech/pyproject.toml:21`, `workers/translategemma/pyproject.toml:21`, and `workers/_shared/pyproject.toml:2` all set `[tool.pytest.ini_options] pythonpath = ["../.."]`. This prepends the project root to `sys.path` for every worker test run, which is why the workspace members (which `uv sync` did not install) can still be imported during `just test`. The same hack is also why DX-003's gap is invisible in CI: pytest finds the workers by walking up to the project root, not by importing an installed package. The hack is brittle in three ways: (a) it couples each worker's test config to the directory layout of the parent repo (e.g. a flat uv workspace install elsewhere would break); (b) it papers over a real install-time bug (DX-003) so the gap will not surface even when a developer tries to import a worker from a REPL or a new package; (c) the `pythonpath` hack duplicates the same one-liner across four files, so a future test-discovery refactor (e.g. switching to `import-mode=importlib` per pyproject.toml:142) has to update all four. AGENTS.md says: "Prefer strict domain separation, avoid string-based dispatch, and use typing in your favor" — the same spirit applies to test config; the right way for a workspace member to be importable is for it to be installed, not for sys.path to be patched.

**Why it matters.** Coupled, repeated test config that exists to paper over an install-time gap. Once DX-003 is fixed (Justfile uses `--all-packages`), the hack becomes redundant — but it stays in the four `pyproject.toml` files unless someone remembers to clean it up. Better to remove the hack as part of the DX-003 fix so the test config does not silently change behaviour in the future.

**Recommendation.** After fixing DX-003 (add `--all-packages` to `Justfile:40` and `.envrc.example:5`), remove `pythonpath = ["../.."]` from all four worker pyproject.toml files. If a worker test ever needs the project root on `sys.path` for a real reason, add a one-line comment explaining which import relies on it; otherwise, the workspace install should be the single source of truth.

**Verification.** After the DX-003 fix and the four `pythonpath` removals, `just install && just test` still passes (643+ tests, coverage >= 80%). `uv run python -c "import workers.qwen3tts.handler"` still works from the project root. `uv run python -c "import workers.qwen3tts.handler"` from outside the worktree (e.g. `cd /tmp`) fails with `ModuleNotFoundError` (the correct behaviour, since it's a workspace install).

### DOC-007 — 24 source files have multi-line module docstrings that violate AGENTS.md's 1-line module-docstring rule

```yaml
status: open
severity: medium
effort: M
reviewed_at: 77aadcd
last_verified_at:
  commit: pending
  date: 2026-06-26
fixed_in: []
files:
  - path: src/acheron/shell/executors/streaming.py
    lines: 1-9
  - path: src/acheron/shell/logging_context.py
    lines: 1-5
  - path: src/acheron/shell/transports/_multipart.py
    lines: 1-3
  - path: src/acheron/tls.py
    lines: 1-5
  - path: src/acheron/worker_sdk/__init__.py
    lines: 1-5
  - path: src/acheron/worker_sdk/_caps.py
    lines: 1-8
  - path: src/acheron/worker_sdk/_edge_http.py
    lines: 1-7
  - path: src/acheron/worker_sdk/_runpod_client.py
    lines: 1-8
  - path: src/acheron/worker_sdk/_server.py
    lines: 1-4
  - path: src/acheron/worker_sdk/artifacts.py
    lines: 1-4
  - path: src/acheron/worker_sdk/cli.py
    lines: 1-8
  - path: src/acheron/worker_sdk/cloud.py
    lines: 1-14
  - path: src/acheron/worker_sdk/config_loader.py
    lines: 1-9
  - path: src/acheron/worker_sdk/inputs.py
    lines: 1-4
  - path: src/acheron/worker_sdk/pricing.py
    lines: 1-5
  - path: src/acheron/worker_sdk/registration.py
    lines: 1-7
  - path: src/acheron/worker_sdk/schemas.py
    lines: 1-3
  - path: src/acheron/worker_sdk/settings.py
    lines: 1-8
  - path: workers/granite_speech/handler.py
    lines: 1-8
  - path: workers/granite_speech/runpod_entrypoint.py
    lines: 1-4
  - path: workers/qwen3tts/handler.py
    lines: 1-8
  - path: workers/qwen3tts/runpod_entrypoint.py
    lines: 1-4
  - path: workers/translategemma/handler.py
    lines: 1-11
  - path: workers/translategemma/runpod_entrypoint.py
    lines: 1-4
related: [DOC-005]
```

**Issue.** AGENTS.md hard rule: "Usually module level docstrings should be 1 line. For function and method docstrings use google style as per ruff config in `pyproject.toml`. Be concise." A scan of non-test Python source under `src/`, `dashboard/`, and `workers/` finds 24 modules with multi-line module docstrings (the second paragraph is the offender; the first line is fine). The pattern is consistent — a short summary line, a blank line, then a 1–13 line paragraph explaining the module's place in the architecture (e.g. `tls.py:3-5` explains why it lives at the top level rather than under `shell/`; `worker_sdk/__init__.py:3-5` explains that importing the SDK transitively loads runpod). Most of these paragraphs are exactly the kind of "stale-prone structural rationale" that AGENTS.md's comment discipline section targets: they reference layout (top-level vs shell), import-linter contracts, or future plans. DOC-005 already flagged one of them — the second paragraph of `src/acheron/tls.py:3-5` — and the fix was supposed to be the canonical example. That fix was not applied (the paragraph is still there), and the same shape was not addressed across the other 23 modules. A reviewer cannot tell from `just lint-strict` that the rule is being violated (ruff's pydocstyle D rules check the opposite — D200/D202 want a one-line summary; they do not flag a 2-line docstring with a blank line in between).

**Why it matters.** The docstrings will go stale as the architecture evolves. For example, `tls.py:3-5` references the `worker-sdk-no-shell` import-linter contract; if that contract is renamed (e.g. to `worker-sdk-no-orchestrator` in a future refactor), the docstring will silently lie. Same for `worker_sdk/__init__.py:3-5` referencing the runpod SDK loading: if the SDK is split or the lazy import is removed, the docstring becomes misleading. AGENTS.md calls this out by name: "Avoid stale-prone comments that reference impl details." A 1-line module docstring that says what the module does (not where it lives or why) is the discipline the project asks for. Bundling 24 sites into one story because the threshold is high and the remediation is mechanical (drop the second paragraph on each).

**Recommendation.** For each of the 24 modules, reduce the module docstring to a single line that names the module's purpose. Examples of the trimmed form: `tls.py` -> `'''TLS helpers — env-var to SSL credentials conversion for HTTP and gRPC.'''`; `worker_sdk/__init__.py` -> `'''Acheron worker SDK — the blueprint for Layer 8 real GPU workers.'''`. The architectural rationale (why a module lives at the top level, why an import is lazy, etc.) belongs in the relevant import-linter contract definition or a top-level design doc, not in a module docstring that has to track every refactor. The fix is `ruff format` + manual trim per file; total effort is ~30 min of mechanical editing.

**Verification.** Run the scan: `for f in $(rg -l '^\"\"\"' src/ dashboard/ workers/ --type py | rg -v '/tests/' | rg -v 'pyproject\.toml'); do head -1 "$f" | python3 -c "import sys; doc=sys.stdin.read(); m=__import__('re').match(r'^\"\"\"(.+?)\"\"\"$', doc); import sys; sys.exit(0 if m and '\n' not in m.group(1) and len(m.group(1)) < 200 else 1)" || echo "$f: multi-line docstring"; done` — output should be empty. `just lint-strict` passes. `grep -c '^\"\"\"$' <file>` continues to find one match per file (the closing triple-quote).
