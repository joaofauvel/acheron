# Phase 4B Readiness Experience Design

**Stories:** OPS-006, OPS-019, MAINT-009
**Status:** Approved design

## Goal

Make worker cold-start state visible and actionable: operators can see BOOTING progress in the dashboard, receive a submission warning when TTS capacity is cold, and see a log breadcrumb before a worker reaches its BOOTING timeout.

## Scope

This bundle follows OPS-007's worker-fleet readiness contract. It does not change OPS-007's HTML status fragment, add submission gating, or implement unrelated submission validation. It covers the shared booting-state data flow used by the dashboard and submission response.

## Architecture

`RegisteredWorker` gains a persisted `booting_since: float | None` timestamp. Both `InMemoryWorkerStore` and `RedisWorkerStore` preserve this field. A worker registration starts healthy with no boot timestamp; entering `BOOTING` sets the timestamp; `HEALTHY`, `OFFLINE`, unregister, and re-registration clear it.

The health monitor uses the persisted timestamp for elapsed-time and timeout decisions. It emits one warning when BOOTING reaches 90% of the ten-minute timeout, then retains the existing timeout behavior and `provider BOOTING timeout exceeded` error when the timeout is reached. Warning state is cleared when the worker leaves BOOTING.

The worker listing API exposes `booting_elapsed_seconds: float | None` and `booting_timeout_seconds: float`. The elapsed value is computed from the persisted timestamp at response time and is never accepted from a client. Existing worker status, authentication, TLS, and error-sanitization behavior remains unchanged.

## Dashboard behavior

The workers partial renders a BOOTING worker with elapsed time and a progress bar whose maximum is the API-provided timeout. The dashboard uses a small dependency-free browser timer to update the displayed elapsed value and progress once per second; HTMX swaps continue to refresh the server snapshot. HEALTHY and OFFLINE rows retain their existing badges and errors.

## Submission behavior

`JobResponse` gains `warnings: list[str]`, defaulting to an empty list for compatibility with existing responses. The job submission route computes warnings from the registered worker fleet after accepting the job. When one or more TTS workers are BOOTING, it returns a warning containing the affected worker IDs and elapsed time, with guidance that cold start typically takes 30–90 seconds. Submission remains permitted and the warning is informational.

The CLI prints returned warnings after the normal job ID, status, and plan lines. Warning rendering never changes a successful submission's exit status. Existing API failures and CLI remediation paths remain unchanged.

## Testing

Use in-process tests and TDD. Store tests cover boot timestamp creation, persistence, clearing, and re-registration. Health-monitor tests cover elapsed timeout behavior and the single near-timeout warning. API tests cover computed elapsed fields and submission warnings for booting TTS workers, including no-warning healthy and non-TTS cases. Dashboard tests cover countdown/progress markup, one-second update behavior, and existing healthy/offline/error rows. CLI tests cover warning rendering without altering successful exit status. Redis tests retain round-trip coverage for the new field.

Run `just validate`, `just ux-validate`, the dashboard first-run step, and `just ux-verify OPS-006`, `OPS-019`, and `MAINT-009`. Preserve the existing non-root, TLS, authentication, Compose, simulator, and first-run contracts.
