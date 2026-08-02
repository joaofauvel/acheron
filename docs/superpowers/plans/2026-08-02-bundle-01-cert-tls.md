# Certificate/TLS Bundle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the `01-cert-tls` bundle for `MAINT-003`, `DEPLOY-008`, and `MAINT-005`: protect operator-owned certificate material, expose expiry signals, and reload the active orchestrator certificate without a process restart.

**Architecture:** `scripts/generate_dev_certs.py` owns development-material protection. A runtime `CertificateManager` in `src/acheron/tls.py` owns certificate parsing, threshold logging, status, and one persistent `ssl.SSLContext`. The shell API, CLI, and Uvicorn server consume that manager instead of independently reading certificate files.

**Tech Stack:** Python 3.14, `cryptography`, `ssl.SSLContext`, FastAPI, Uvicorn, Click, Pydantic, pytest, Docker Compose, `uv`.

## Global Constraints

- Preserve the corrected UX journeys and keep `DEPLOY-002` and `MAINT-004` obsolete; do not add production SAN generation.
- Use TDD and one writer per worktree; do not add compatibility fallbacks that hide an invalid certificate source.
- Use `uv` for dependency changes; promote the existing `cryptography~=46.0` dev dependency to the runtime dependency list.
- Never log certificate private keys, registration tokens, or raw certificate contents.
- A failed regeneration or reload must leave the previously valid material/context usable.
- The threshold contract is 30 days (`WARNING`), 7 days (`WARNING` once per threshold), 1 day (`ERROR`), and expiry (`CRITICAL`).
- Run `just validate`, the required cert/Compose first-run checks, and `just ux-validate` before marking stories verified.

## File Map

- `scripts/generate_dev_certs.py` — marker, no-op, refusal, and explicit force semantics.
- `src/acheron/tls.py` — certificate status, threshold monitor, and reloadable context.
- `src/acheron/shell/api/app.py` — manager lifecycle/state.
- `src/acheron/shell/api/routes/admin.py` — admin-protected status/reload endpoints.
- `src/acheron/core/schemas.py` and `src/acheron/shell/api/schemas.py` — shared wire/request models.
- `src/acheron/api_client.py` and `src/acheron/cli.py` — `certs status`/`certs reload` surfaces.
- `src/acheron/worker_sdk/_server.py` and `src/acheron/shell/api/__main__.py` — Uvicorn context wiring.
- `docker-compose.yml`, `Justfile`, and `README.md` — documented generation/operation contract.
- `tests/scripts/test_generate_dev_certs.py`, `tests/test_tls.py`, `tests/shell/api/test_admin.py`, `tests/worker_sdk/test_server.py`, `tests/test_api_client.py`, `tests/shell/test_cli.py`, `tests/integration/test_tls.py`, and `tests/first_run/test_2_compose_start.py` — behavior and journey evidence.
- `docs/ux_review/deploy.md`, `docs/ux_review/maint.md`, and `docs/ux_review/summary.md` — metadata only after evidence.

## Tasks

### Task 1: Move certificate parsing to the runtime dependency set

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [ ] Remove `cryptography~=46.0` from the `dev` dependency group with `uv remove --dev cryptography`.
- [ ] Add `cryptography~=46.0` to runtime dependencies with `uv add "cryptography~=46.0"`.
- [ ] Run `uv lock` and confirm the lockfile contains one runtime entry.
- [ ] Run `uv run python -c "import cryptography; print(cryptography.__version__)"`.
- [ ] Commit with `git commit -m "build(tls): make certificate parsing a runtime dependency"`.

### Task 2: Write failing tests for safe development-certificate generation

**Files:**
- Test: `tests/scripts/test_generate_dev_certs.py`

- [ ] Replace the current overwrite expectation in `test_idempotent` with `test_second_generation_is_a_noop_without_rewriting_files`; record bytes and `st_mtime_ns` for the CA and one service certificate.
- [ ] Add `test_first_generation_creates_dev_marker`; assert `.dev-ca` and the existing CA/service files are created.
- [ ] Add `test_unmarked_existing_ca_refuses_to_overwrite`; write sentinel CA/key bytes, run the generator, assert nonzero failure, and assert sentinel bytes remain unchanged.
- [ ] Add `test_marked_dev_bundle_can_be_forced`; run once, run again with `--force`, and assert the generated certificate changes only on the explicit force path.
- [ ] Add `test_incomplete_marked_bundle_fails_without_rewriting`; remove one managed certificate after marker creation and assert the error names the missing file.
- [ ] Run `uv run pytest --no-cov tests/scripts/test_generate_dev_certs.py -q`; confirm the new tests fail against the current unconditional generator.

### Task 3: Implement the safe generator contract

**Files:**
- Modify: `scripts/generate_dev_certs.py`

- [ ] Add a constant for the marker name `.dev-ca` and a preflight helper that distinguishes a fresh output directory, a complete marked bundle, and existing unmarked/operator material.
- [ ] Make `generate()` preflight before `_build_ca`; return without rewriting a complete marked bundle unless `force=True`.
- [ ] Refuse an existing unmarked CA/key or partial bundle with an actionable error that says how to remove development material or pass explicit `--force`; never overwrite it.
- [ ] Write `.dev-ca` only after every CA, key, and service certificate has been generated and permissioned successfully; remove a partial marker on failure.
- [ ] Add a Click/argparse `--force` option and pass it to `generate(force=...)`; keep the default safe.
- [ ] Preserve the existing service names, SANs, key permissions, certificate permissions, and output filenames.
- [ ] Run `uv run pytest --no-cov tests/scripts/test_generate_dev_certs.py -q`; expect all generator tests to pass.
- [ ] Commit with `git commit -m "fix(DEPLOY-008): protect operator certificate material"`.

### Task 4: Wire safe generation through Compose and Just

**Files:**
- Modify: `docker-compose.yml`
- Modify: `Justfile`
- Test: `tests/first_run/test_2_compose_start.py`

- [ ] Update `certs-init` comments to describe first-run generation and no-op reuse; remove wording that implies unconditional overwrite is safe.
- [ ] Keep `certs-init`’s successful-completion dependency unchanged so both first generation and no-op reuse gate startup.
- [ ] Update the `certs` recipe to forward an optional argument to the generator, allowing `just certs --force` only when explicitly requested.
- [ ] Add `test_compose_reuses_marked_development_bundle`; start the Compose step twice and compare the marker/CA timestamps or hashes.
- [ ] Add `test_compose_rejects_unmarked_certificate_material`; seed operator-owned sentinel files and assert certs-init fails before orchestrator startup.
- [ ] Run `just first-run --step 2` and `docker compose config`; confirm both pass for a fresh marked bundle.
- [ ] Commit with `git commit -m "fix(DEPLOY-008): make Compose certificate initialization non-destructive"`.

### Task 5: Write failing certificate-manager unit tests

**Files:**
- Test: `tests/test_tls.py`

- [ ] Add a `tmp_path` fixture that creates a CA and server certificate with controlled `not_valid_after` values through `cryptography.x509`.
- [ ] Add a mutable UTC clock fixture so tests never sleep or depend on wall-clock thresholds.
- [ ] Add `test_certificate_status_reports_subject_and_remaining_time`.
- [ ] Add `test_certificate_status_formats_sub_day_remaining_time`.
- [ ] Add `test_certificate_monitor_logs_startup_info`.
- [ ] Add `test_certificate_monitor_emits_30_day_warning`, `test_certificate_monitor_emits_7_day_warning_once`, `test_certificate_monitor_emits_1_day_error`, and `test_certificate_monitor_emits_expiry_critical`.
- [ ] Add `test_certificate_manager_reload_rejects_invalid_pair_without_mutation`.
- [ ] Add `test_manager_is_disabled_when_tls_pair_is_unset`.
- [ ] Run `uv run pytest --no-cov tests/test_tls.py -q`; confirm the new tests fail because no manager/status interface exists.

### Task 6: Implement the shared certificate manager

**Files:**
- Modify: `src/acheron/tls.py`
- Test: `tests/test_tls.py`

**Interfaces:**
- Produces `CertificateStatus` with path/name, subject, `expires_at`, remaining time, and severity.
- Produces `CertificateManager.status(now: datetime | None = None) -> CertificateStatus | None`.
- Produces `CertificateManager.reload() -> CertificateStatus`.
- Produces `CertificateManager.check_and_log(now: datetime | None = None) -> CertificateStatus | None`.
- Produces async `CertificateManager.start()`/`stop()` for the daily monitor.

- [ ] Add typed value objects using concrete types and `Literal`/enum severity values; do not use `Any`.
- [ ] Parse the active PEM certificate with `cryptography.x509`, normalize all timestamps to UTC, and make `now` injectable.
- [ ] Emit startup `INFO` with certificate name, subject, expiry, and remaining time.
- [ ] Track emitted thresholds per manager instance so the daily loop does not duplicate the 30/7/1/0-day messages.
- [ ] Validate a replacement cert/key pair into a temporary `SSLContext` before calling `load_cert_chain()` on the persistent active context.
- [ ] Preserve the old context when parsing or loading the replacement fails; raise a chained typed Acheron error with remediation.
- [ ] Make a missing TLS pair return `None`/disabled status through an explicit contract rather than reporting a healthy certificate.
- [ ] Run the focused TLS tests and confirm all threshold/reload tests pass.
- [ ] Commit with `git commit -m "feat(MAINT-003): add certificate status and expiry monitoring"`.

### Task 7: Wire monitoring into the shell application lifecycle

**Files:**
- Modify: `src/acheron/shell/api/app.py`
- Modify: `src/acheron/shell/orchestrator.py` only for typed manager ownership if required by the existing constructor.
- Test: `tests/shell/api/test_main.py`

- [ ] Add a certificate-manager injection parameter to `create_app(...)` and store it on `app.state`.
- [ ] Start the manager after the orchestrator has initialized and stop it before application shutdown; preserve existing shutdown ordering and exception isolation.
- [ ] Add `test_app_starts_and_stops_certificate_monitor_without_leaking_task`.
- [ ] Add `test_app_without_tls_keeps_existing_startup_behavior`.
- [ ] Run `uv run pytest --no-cov tests/shell/api/test_main.py tests/test_tls.py -q`.
- [ ] Commit with `git commit -m "feat(MAINT-003): attach certificate monitoring to app lifecycle"`.

### Task 8: Add admin certificate status and reload routes

**Files:**
- Modify: `src/acheron/core/schemas.py`
- Modify: `src/acheron/shell/api/schemas.py`
- Modify: `src/acheron/shell/api/routes/admin.py`
- Test: `tests/shell/api/test_admin.py`

- [ ] Add shared response models for TLS-enabled state, certificate status, and reload result; ensure no private key path or PEM content is returned.
- [ ] Add `GET /admin/certs/status` protected by `AdminTokenDep`.
- [ ] Add `POST /admin/certs/reload` protected by `AdminTokenDep` and route it through `execute_admin_action` so successful reloads and failures have request/audit context.
- [ ] Return the existing structured `AdminErrorResponse` for disabled TLS, invalid replacement material, and missing admin credentials.
- [ ] Add `test_cert_status_requires_admin_token`, `test_cert_status_returns_expiry_metadata`, `test_cert_reload_requires_admin_token`, and `test_cert_reload_preserves_structured_error_on_invalid_pair`.
- [ ] Run `uv run pytest --no-cov tests/shell/api/test_admin.py -q`.
- [ ] Commit with `git commit -m "feat(MAINT-005): add admin certificate status and reload routes"`.

### Task 9: Expose certificate status/reload through the client and CLI

**Files:**
- Modify: `src/acheron/api_client.py`
- Modify: `src/acheron/cli.py`
- Test: `tests/test_api_client.py`
- Test: `tests/shell/test_cli.py`

- [ ] Add `AcheronClient.get_cert_status()` and `AcheronClient.reload_certs()`, both using `_admin_headers()` and the shared response models.
- [ ] Add the `certs` Click group with `acheron certs status` and `acheron certs reload`.
- [ ] Require `ACHERON_ADMIN_TOKEN`; never fall back to `ACHERON_REGISTRATION_TOKEN`.
- [ ] Render `orchestrator.crt expires in 0d 0h 14m`-style output without printing secrets or PEM paths unnecessarily.
- [ ] Add `test_get_cert_status_uses_admin_header`, `test_reload_certs_posts_admin_header`, `test_certs_status_renders_expiry`, `test_certs_reload_renders_success`, and `test_certs_commands_require_admin_token`.
- [ ] Run the focused client/CLI tests and `acheron certs status --help`.
- [ ] Commit with `git commit -m "feat(MAINT-003): expose certificate status and reload commands"`.

### Task 10: Connect the persistent context to Uvicorn

**Files:**
- Modify: `src/acheron/worker_sdk/_server.py`
- Modify: `src/acheron/shell/api/__main__.py`
- Test: `tests/worker_sdk/test_server.py`
- Test: `tests/integration/test_tls.py`

- [ ] Add the explicit manager/context seam to `run_worker_server()` while preserving the existing file-path path for worker/test callers.
- [ ] Configure the orchestrator listener with the manager’s persistent `ssl.SSLContext`; ensure `/admin/certs/reload` updates that exact object.
- [ ] Add `test_server_uses_reloadable_context_when_manager_present` and `test_server_preserves_plain_http_without_tls`.
- [ ] Extend the integration TLS fixture to issue a replacement server certificate signed by the existing test CA.
- [ ] Add `test_orchestrator_cert_reload_keeps_pid_and_worker_connectivity`; assert same PID, new peer certificate metadata, healthy API, and registered HTTP/gRPC workers after reload.
- [ ] Run `uv run pytest --no-cov tests/worker_sdk/test_server.py tests/integration/test_tls.py -q`.
- [ ] Commit with `git commit -m "feat(MAINT-005): reload active TLS context without restart"`.

### Task 11: Update operator documentation and UX metadata

**Files:**
- Modify: `README.md`
- Modify: `docker-compose.yml`
- Modify: `Justfile`
- Modify: `docs/superpowers/specs/layer-7c-tls.md`
- Modify: `docs/ux_review/deploy.md`
- Modify: `docs/ux_review/maint.md`
- Modify: `docs/ux_review/summary.md`

- [ ] Document development-only non-destructive generation, `.dev-ca`, explicit `--force`, `acheron certs status`, and `acheron certs reload`.
- [ ] Keep production documentation on externally managed SAN-correct certificates; do not revive `MAINT-004` or `DEPLOY-002`.
- [ ] Refresh the three story citations after implementation changes and retain bundle IDs.
- [ ] Set `fixed_in` only after implementation commits and set `verified_in`/`verified_by` only after the independent cert/rotation journey.
- [ ] Run `just validate`, `just first-run --step 2`, `just ux-validate`, and `git diff --check`.
- [ ] Commit with `git commit -m "docs(ux-review): close certificate bundle evidence"`.

## Completion Gate

- [ ] `just validate` passes.
- [ ] `just first-run --step 2` passes.
- [ ] Manual status → replacement → reload → same-PID → worker-connectivity journey is recorded by an independent verifier.
- [ ] `just ux-validate` and the relevant `just ux-verify` commands pass.
- [ ] A fresh-context correctness review and documentation-staleness review report no blockers.
