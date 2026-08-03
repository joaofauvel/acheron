# Bundle 02 token-auth independent verification

Date: 2026-08-03

Verification target: `d17f27cb2b1f6d25c056eac5d35a8c8ecc7c6db7` (`HEAD`), repository tree fingerprint `b98bab651b96ce43831ae4f5b47b7b9cd16720f15194a3649821bdd6fb772beb`.

Fresh verification was performed from the isolated `ux-tackle/maint-bundle-02` checkout after the Task 9 fix rounds. No plaintext registration or admin token was recorded.

## Evidence

- `just first-run`: full Quick Start, Compose, and success-criteria journey passed (20 tests).
- File-backed status → API rotation with a reason → audit history passed.
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
- `just ux-validate` — passed after attestation refresh.
- `just ux-verify MAINT-007` — passed.
- `just ux-verify MAINT-006` — passed.
- `just ux-verify DEPLOY-012` — passed.
- `git diff --check` — passed.

## Fix-round scope

- Fix commit: `d17f27c` (`fix(MAINT-007): scope insecure bearer transport`).
- Earlier Task 9 evidence/fix commits are included in the reviewed range `8c4d77e..d17f27c`.
- Outbound bearer checks now require an explicit URL/target hostname allowlist; server-only warning suppression remains separate.

## Residual risks

- First-run cleanup emits non-fatal permission warnings for container-owned development certificates.
- The Compose local-edge allowlist is intentionally deployment-specific; remote plaintext endpoints remain refused unless an operator explicitly configures HTTPS or a separate allowlist.
- This evidence covers the isolated local Compose and test journey, not external production rotation.
