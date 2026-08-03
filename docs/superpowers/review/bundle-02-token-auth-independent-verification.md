# Bundle 02 token-auth independent verification

Date: 2026-08-03

Fresh verification was performed from the isolated `ux-tackle/maint-bundle-02` checkout after the Task 9 fix round. No plaintext registration or admin token was recorded.

## Evidence

- `just first-run`: full Quick Start, Compose, and success-criteria journey passed (20 tests).
- File-backed status → API rotation with a reason → audit history passed.
- Every supported Compose edge (`tts-local-stub`, `asr-local-stub`, `translation-local-stub`, `tts-runpod-stub`, `translation-runpod-stub`, and `tts-grpc-stub`) accepted the current token and rejected the superseded token.
- Container identities were captured for every supported edge before rotation and were unchanged after rotation; this demonstrated reload/rollout without container restart.
- A real authenticated `POST /inputs` → `POST /jobs` → `GET /jobs/{job_id}` journey completed successfully with non-empty output after rotation.
- Compose uses an explicit local-edge hostname allowlist for plaintext worker transport; the bearer transport guard consults the allowlist even when the orchestrator's TLS warning-suppression flag is set. Focused transport tests reject a remote plaintext bearer endpoint.
- Admin status and CLI status output, audit responses, worker responses, job output, and Compose logs contained no plaintext credentials.

## Commands

- `just first-run` — passed: 20 passed, with existing container-owned certificate cleanup warnings.
- Focused token/worker/admin/client/CLI tests — passed.
- `just validate` — passed.
- `just ux-validate` — passed.
- `just ux-verify MAINT-007 MAINT-006 DEPLOY-012` — passed.
- Ruff, format, type checks, and `git diff --check` — passed.

## Residual risks

- First-run cleanup emits non-fatal permission warnings for container-owned development certificates.
- The Compose local-edge allowlist is intentionally deployment-specific; remote plaintext endpoints remain refused unless an operator explicitly configures HTTPS or a separate allowlist.
