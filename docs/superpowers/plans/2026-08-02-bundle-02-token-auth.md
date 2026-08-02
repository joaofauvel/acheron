# Token/Auth Bundle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `02-token-auth` for `MAINT-007`, `MAINT-006`, and `DEPLOY-012`: make token configuration shell-restart-safe, make Compose reach file-backed auto-mint, and provide safe auditable rotation across worker edges.

**Architecture:** File-backed tokens are the rotatable Compose mode; explicit environment tokens remain static and are never silently overwritten or reported as rotated. `RegistrationTokenStore` owns persistence, metadata, fingerprints, and audit history. A worker-side provider reads an explicit env token first and otherwise reads a mounted token file per authentication operation, allowing coordinated file replacement without mutating another process’s environment.

**Tech Stack:** Python 3.14, FastAPI, Click, Pydantic, Redis/Compose volumes, httpx, pytest, `uv`.

## Global Constraints

- Preserve standalone-worker support for `ACHERON_WORKER__REGISTRATION_TOKEN`; static env mode must remain explicit and documented.
- Never return or log plaintext registration tokens; audit records contain only timestamps, reasons, worker IDs, and short SHA-256 fingerprints.
- Do not claim env-mode rotation succeeds: the existing process environment and arbitrary worker containers cannot be mutated by the orchestrator.
- Use one source-of-truth contract for Compose, dashboard proxy requests, orchestrator transports, worker registration, and worker `/execute` authentication.
- Use TDD, typed domain objects, chained exceptions, and no `Any`/string-based dispatch.
- Run `just validate`, the three token/Compose first-run steps, relevant UX verification, and `just ux-validate` before closing stories.

## File Map

- New: `src/acheron/shell/token_auth.py` — persisted token state, source mode, status, rotation, and audit records.
- New: `src/acheron/worker_sdk/token_auth.py` — env/file provider used by registration and edge auth.
- Modify: `src/acheron/shell/orchestrator.py` — store integration and rollout coordination.
- Modify: `src/acheron/worker_sdk/settings.py`, `registration.py`, `app.py`, `_edge_http.py` — file-backed provider and live validation.
- Modify: `dashboard/app.py` and `docker-compose.yml` — server-side file lookup and shared read-only volume.
- Modify: `src/acheron/core/errors.py`, `src/acheron/core/schemas.py`, `src/acheron/shell/api/schemas.py`, `src/acheron/shell/api/routes/admin.py` — typed admin contract.
- Modify: `src/acheron/api_client.py`, `src/acheron/cli.py` — token status/rotation commands.
- Modify: `README.md`, `.env.example`, `acheron.yaml.example`, Compose documentation, and worker READMEs — source-of-truth docs.
- Tests: shell token store/orchestrator/admin, worker provider/app/edge, API client/CLI, Compose config, and first-run journey tests.
- Metadata last: `docs/ux_review/maint.md`, `docs/ux_review/deploy.md`, `docs/ux_review/summary.md`.

## Tasks

### Task 1: Write the source-of-truth contract tests and documentation matrix

**Files:**
- Test: `tests/shell/test_token_auth.py`
- Test: `tests/first_run/test_1_quick_start.py`
- Modify: `README.md`
- Modify: `.env.example`
- Modify: `acheron.yaml.example`

- [ ] Add a contract table to the tests/docs that distinguishes:
  - explicit `ACHERON_REGISTRATION_TOKEN`/worker env token: static, externally managed;
  - unset Compose token: file-backed auto-mint at `<data_dir>/.registration_token`;
  - worker `ACHERON_WORKER__REGISTRATION_TOKEN_FILE`: reload-aware file source.
- [ ] Add failing assertions that Quick Start does not require a shell-only `export` for subsequent terminals.
- [ ] Add failing assertions that status output reports `source=environment` or `source=file` without token values.
- [ ] Document that env-mode `acheron token rotate` exits nonzero with remediation to update/restart workers externally.
- [ ] Run `uv run pytest --no-cov tests/first_run/test_1_quick_start.py -q`; confirm the new persistence assertions fail against the current README/Compose contract.

### Task 2: Implement the persisted token store

**Files:**
- Create: `src/acheron/shell/token_auth.py`
- Test: `tests/shell/test_token_auth.py`

**Interfaces:**
- `TokenSource = Literal["environment", "file"]`.
- `RegistrationTokenStatus` contains source, created timestamp, last-rotation timestamp, rotation count, and token fingerprint; it never contains plaintext.
- `RegistrationTokenAudit` contains timestamp, reason, old/new fingerprint prefixes, worker IDs, result, and request ID.
- `RegistrationTokenStore.load_or_create(configured_token: str | None) -> str`.
- `RegistrationTokenStore.status() -> RegistrationTokenStatus`.
- `RegistrationTokenStore.rotate(reason: str, request_id: str, rollout: Callable[[str], Awaitable[RolloutResult]]) -> RegistrationTokenStatus`.
- `RegistrationTokenStore.read_current() -> str`.

- [ ] Add failing tests: `test_file_backed_token_is_created_with_metadata_and_0600_permissions`, `test_existing_file_backed_token_is_reused_without_regeneration`, `test_environment_token_is_not_written_or_rotated`, `test_status_redacts_token`, and `test_rotation_writes_secret_free_audit`.
- [ ] Use `<data_dir>/.registration_token` for the secret and a separate bounded JSONL metadata/history file; write the secret atomically and enforce mode `0600`.
- [ ] Keep old single-line token files readable; treat a non-empty configured env token as environment source and never overwrite it.
- [ ] Use short SHA-256 fingerprints in status/audit; never put the secret in exception text.
- [ ] Make rotation write candidate state atomically, invoke the rollout callback, and restore the previous token/audit result when rollout fails.
- [ ] Add chained `TokenRotationError`/`TokenStoreError` domain errors for filesystem and rollout failures.
- [ ] Run `uv run pytest --no-cov tests/shell/test_token_auth.py -q` and confirm all store tests pass.
- [ ] Commit with `git commit -m "feat(MAINT-007): add persisted token lifecycle"`.

### Task 3: Integrate the store with the orchestrator

**Files:**
- Modify: `src/acheron/shell/orchestrator.py`
- Test: `tests/shell/test_orchestrator.py`

- [ ] Replace `_load_or_create_registration_token()` internals with `RegistrationTokenStore.load_or_create`, preserving the existing 32-character validation and redaction behavior.
- [ ] Add typed `registration_token_status()` and `rotate_registration_token(reason, request_id)` methods.
- [ ] Keep the existing dynamic `registration_token_provider` used by shell transports so a successful file-backed rotation updates in-memory dispatch credentials.
- [ ] Add an injected `WorkerRotationCoordinator` seam that discovers registered remote HTTP edges, presents the candidate credential through an authenticated check, and returns a typed rollout result.
- [ ] Treat unsupported transport types as an explicit failed rollout with remediation; do not report a mixed HTTP/gRPC fleet as rotated without verification.
- [ ] Add `test_start_uses_file_store_when_env_token_is_unset`, `test_start_preserves_explicit_environment_token`, `test_rotation_rejects_environment_source`, and `test_rotation_rolls_back_when_worker_rollout_fails`.
- [ ] Run `uv run pytest --no-cov tests/shell/test_orchestrator.py tests/shell/test_token_auth.py -q`.
- [ ] Commit with `git commit -m "feat(MAINT-007): coordinate orchestrator token rotation"`.

### Task 4: Add the worker token provider and live edge authentication

**Files:**
- Create: `src/acheron/worker_sdk/token_auth.py`
- Modify: `src/acheron/worker_sdk/settings.py`
- Modify: `src/acheron/worker_sdk/registration.py`
- Modify: `src/acheron/worker_sdk/app.py`
- Modify: `src/acheron/worker_sdk/_edge_http.py`
- Test: `tests/worker_sdk/test_token_auth.py`
- Test: `tests/worker_sdk/test_settings.py`
- Test: `tests/worker_sdk/test_registration.py`
- Test: `tests/worker_sdk/test_app.py`
- Test: `tests/worker_sdk/test_edge_http.py`

**Interfaces:**
- `RegistrationTokenProvider.current() -> str | None`.
- `EnvironmentOrFileTokenProvider(env_token: str | None, token_file: Path | None).current() -> str | None`.

- [ ] Add `registration_token_file: Path | None` to `WorkerSettings` with `ACHERON_WORKER__REGISTRATION_TOKEN_FILE` mapping.
- [ ] Normalize empty interpolated values to unset; prefer a non-empty explicit env token, otherwise read the file on every `.current()` call.
- [ ] Pass the provider to `register_with_orchestrator()` and `EdgeApp` without changing standalone env-token behavior.
- [ ] Change bearer verification for `/execute` to resolve the current provider token per request.
- [ ] Add an authenticated lightweight `/auth/check` endpoint for rollout verification; keep `/health` and `/capabilities` public as they are today.
- [ ] Add `test_provider_prefers_nonempty_env_token`, `test_provider_reads_latest_file_value`, `test_execute_uses_latest_file_token_without_restart`, `test_execute_rejects_old_token_after_rotation`, and `test_auth_check_requires_current_bearer_token`.
- [ ] Run the focused worker SDK tests and verify no plaintext token appears in logs/errors.
- [ ] Commit with `git commit -m "feat(MAINT-007): support reloadable worker token files"`.

### Task 5: Change Compose token distribution and dashboard lookup

**Files:**
- Modify: `docker-compose.yml`
- Modify: `dashboard/app.py`
- Test: `tests/first_run/test_2_compose_start.py`
- Test: `tests/first_run/test_3_success_criteria.py`
- Test: `dashboard/tests/test_dashboard.py`

- [ ] Replace every `${ACHERON_REGISTRATION_TOKEN:?…}` interpolation with an optional value so the orchestrator can auto-mint when no token is supplied.
- [ ] Add `ACHERON_WORKER__REGISTRATION_TOKEN_FILE=/data/jobs/.registration_token` to each supported worker/edge and mount `acheron-data:/data:ro` for workers and dashboard while retaining the orchestrator’s writable mount.
- [ ] Keep explicit env-mode behavior available for deployments that intentionally provide a static token.
- [ ] Add dashboard server-side file fallback for `ACHERON_REGISTRATION_TOKEN_FILE`; resolve it per protected request and never include the value in HTML/logs/errors.
- [ ] Add `test_compose_config_allows_unset_registration_token`, `test_compose_mounts_shared_token_volume_for_workers`, `test_dashboard_file_token_fallback`, and `test_first_run_auto_mints_and_registers_all_workers`.
- [ ] Run `docker compose config --format json`, `just first-run --step 1`, `just first-run --step 2`, and `just first-run --step 3`.
- [ ] Commit with `git commit -m "fix(MAINT-006): enable Compose token auto-mint"`.

### Task 6: Add admin token status and rotation API

**Files:**
- Modify: `src/acheron/core/errors.py`
- Modify: `src/acheron/core/schemas.py`
- Modify: `src/acheron/shell/api/schemas.py`
- Modify: `src/acheron/shell/api/routes/admin.py`
- Test: `tests/shell/api/test_admin.py`
- Test: `tests/shell/api/test_schemas.py`

- [ ] Add `TokenRotateRequest(reason: str)` with a finite maximum length and non-empty validation.
- [ ] Add typed status/history and rollout response models that exclude plaintext and raw filesystem secrets.
- [ ] Add `GET /admin/token/status` and `POST /admin/token/rotate` protected by `AdminTokenDep`.
- [ ] Route rotation through `execute_admin_action`, record request ID/reason/worker result once, and preserve existing bounded admin-audit behavior.
- [ ] Return structured remediation for environment-source rotation, unsupported worker transport, and failed rollout.
- [ ] Add `test_token_status_requires_admin_token`, `test_registration_token_cannot_authorize_token_routes`, `test_token_rotate_audits_success_once`, and `test_token_rotate_returns_structured_rollout_failure`.
- [ ] Run `uv run pytest --no-cov tests/shell/api/test_admin.py tests/shell/api/test_schemas.py -q`.
- [ ] Commit with `git commit -m "feat(MAINT-007): add admin token lifecycle endpoints"`.

### Task 7: Add API client and CLI commands

**Files:**
- Modify: `src/acheron/api_client.py`
- Modify: `src/acheron/cli.py`
- Test: `tests/test_api_client.py`
- Test: `tests/shell/test_cli.py`

- [ ] Add `AcheronClient.get_registration_token_status()` and `AcheronClient.rotate_registration_token(reason: str)` using only `_admin_headers()`.
- [ ] Add the `token` Click group with `acheron token status` and `acheron token rotate --reason TEXT`.
- [ ] Require `ACHERON_ADMIN_TOKEN`; do not fall back to the registration token.
- [ ] Render source, creation/rotation timestamps, fingerprints, reason, worker counts, and remediation without the token value.
- [ ] Add `test_cli_token_status_uses_admin_header`, `test_cli_token_rotate_renders_rollout`, `test_cli_token_rotate_returns_nonzero_on_failure`, and `test_api_client_does_not_send_registration_token_to_admin_routes`.
- [ ] Run `acheron token --help` and focused API/CLI tests.
- [ ] Commit with `git commit -m "feat(MAINT-007): expose token status and rotation commands"`.

### Task 8: Make Quick Start and worker documentation shell-restart-safe

**Files:**
- Modify: `README.md`
- Modify: `.env.example`
- Modify: `acheron.yaml.example`
- Modify: `docs/superpowers/specs/docker-compose.md`
- Modify: `workers/qwen3tts/README.md`
- Modify: `workers/granite_speech/README.md`
- Modify: `workers/translategemma/README.md`
- Test: `tests/first_run/test_1_quick_start.py`

- [ ] Replace shell-only token export instructions with the file-backed Compose path and explain the named-volume location.
- [ ] Document `acheron token status` and `acheron token rotate --reason` plus the explicit env-mode limitation.
- [ ] Distinguish repository Compose worker token-file mapping from standalone worker env configuration in each worker README.
- [ ] Add `test_quick_start_commands_are_shell_restart_safe` and assert no duplicate `.env` token assignment is recommended.
- [ ] Run `just first-run --step 1` and the docs consistency tests.
- [ ] Commit with `git commit -m "docs(DEPLOY-012): persist Compose token configuration"`.

### Task 9: Verify rotation and update UX metadata

**Files:**
- Modify: `tests/first_run/test_3_success_criteria.py`
- Modify: `tests/shell/test_orchestrator.py`
- Modify: `docs/ux_review/maint.md`
- Modify: `docs/ux_review/deploy.md`
- Modify: `docs/ux_review/summary.md`

- [ ] Add a first-run/Compose journey that reads `/data/jobs/.registration_token`, verifies all supported worker edges register, rotates with a reason, and dispatches a test job with the new token.
- [ ] Run `just validate`, `just first-run`, and the focused token/worker/admin tests.
- [ ] Independently perform `acheron token status`, `acheron token rotate --reason "test"`, audit-history inspection, worker re-registration/reload, and a successful dispatch.
- [ ] Refresh file/line citations and set `fixed_in`, `verified_in`, `last_verified_at`, and `verified_by` only after evidence.
- [ ] Keep bundle `02-token-auth` and the corrected story journeys intact.
- [ ] Run `just ux-validate`, `just ux-verify MAINT-007`, `just ux-verify MAINT-006`, `just ux-verify DEPLOY-012`, and `git diff --check`.
- [ ] Commit with `git commit -m "docs(ux-review): close token authentication bundle evidence"`.

## Completion Gate

- [ ] Compose starts with no exported token and persists one file-backed credential.
- [ ] A new shell can rerun Compose without re-exporting or appending duplicate assignments.
- [ ] Explicit env-token mode is documented as static and rejects unsupported in-place rotation.
- [ ] File-backed rotation updates every supported edge, records audit metadata, and preserves dispatch.
- [ ] `just validate`, all three first-run steps, `just ux-validate`, and the three UX verification commands pass.
- [ ] Fresh-context correctness and documentation-staleness reviews report no blockers.
