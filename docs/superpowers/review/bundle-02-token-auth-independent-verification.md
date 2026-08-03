# Bundle 02 token-auth independent verification

Date: 2026-08-03

Verified code target: `6fda4f1dbdc470e5048fa68ef06b8a6953bb4b00`, repository tree fingerprint `733c849e5d5054bc5be36eef118e4d32fef452d715f2d7d34e28031c15bbced3`.

Fresh verification was performed from the isolated `ux-tackle/maint-bundle-02` checkout at the verified code target after the Task 9 fix rounds. The final UX metadata records the current branch head separately. No plaintext registration or admin token was recorded.

## Evidence

- `just first-run`: full Quick Start, Compose, and success-criteria journey passed (20 tests), including the API and CLI rotation paths.
- File-backed status → API rotation with a reason → audit history passed, followed by `docker compose exec -T orchestrator acheron token rotate --reason test` with sanitized successful output.
- Every supported Compose edge (`tts-local-stub`, `asr-local-stub`, `translation-local-stub`, `tts-runpod-stub`, `translation-runpod-stub`, and `tts-grpc-stub`) accepted the current token and rejected the superseded token.
- Container identities were captured for every supported edge before rotation and were unchanged after rotation; this demonstrated reload/rollout without container restart.
- A real authenticated `POST /inputs` → `POST /jobs` → `GET /jobs/{job_id}` journey completed successfully with non-empty output after rotation.
- Compose uses an explicit local-edge hostname allowlist for plaintext worker transport. The global `ACHERON_ALLOW_INSECURE=1` flag is not injected into Compose and cannot authorize bearer transmission to remote plaintext endpoints. Focused transport tests reject a remote plaintext bearer endpoint even when that flag is set.
- Admin status and CLI status output, audit responses, worker responses, job output, and Compose logs contained no plaintext credentials.

## Commands

- `uv run pytest --no-cov tests/test_tls.py tests/shell/test_orchestrator.py tests/worker_sdk/test_registration.py tests/worker_sdk/test_app.py tests/shell/test_http_worker.py -q` — passed: 162 tests.
- `uv run pytest --no-cov tests/integration/test_worker_integration.py -q` — passed: 9 tests.
- `just validate` — passed: 1874 passed, 20 skipped; lint, import, mypy, basedpyright, and coverage gates passed.
- `just first-run` — passed: 20 passed; existing container-owned certificate cleanup warnings only.
- `docker compose exec -T orchestrator acheron token rotate --reason test` — passed in the file-backed journey; output was sanitized and contained no token value.
- `just ux-validate` — passed after attestation refresh.
- `just ux-verify MAINT-007` — passed.
- `just ux-verify MAINT-006` — passed.
- `just ux-verify DEPLOY-012` — passed.
- `git diff --check` — passed.

## Fix-round scope

- Security fix commit: `d17f27c` (`fix(MAINT-007): scope insecure bearer transport`).
- CLI evidence commit: `6fda4f1` (`test(MAINT-007): verify cli token rotation`).
- Earlier Task 9 evidence/fix commits are included in the reviewed range `8c4d77e..6fda4f1`.
- Outbound bearer checks now require an explicit URL/target hostname allowlist; server-only warning suppression remains separate.

## Residual risks

- First-run cleanup emits non-fatal permission warnings for container-owned development certificates.
- The Compose local-edge allowlist is intentionally deployment-specific; remote plaintext endpoints remain refused unless an operator explicitly configures HTTPS or a separate allowlist.
- This evidence covers the isolated local Compose and test journey, not external production rotation.
