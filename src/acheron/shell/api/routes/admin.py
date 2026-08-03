"""Operator-only job recovery and archive routes."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Literal, Protocol, cast

from fastapi import APIRouter, HTTPException, Request

from acheron.core.errors import (
    AcheronError,
    JobNotFoundError,
    sanitise_exc_message,
    sanitise_public_remediation,
)
from acheron.core.schemas import (
    AdminJobResponse,
    CertificateReloadResponse,
    CertificateStatusResponse,
    CleanupCandidateResponse,
    CleanupFailureResponse,
    CleanupResponse,
    ReapStaleResponse,
    RegistrationTokenAuditResponse,
    RegistrationTokenRolloutResponse,
    RegistrationTokenRotationResponse,
    RegistrationTokenStatusResponse,
)
from acheron.shell.api.admin_audit import AdminAuditDetails, execute_admin_action
from acheron.shell.api.deps import AdminTokenDep, OrchestratorDep  # noqa: TC001
from acheron.shell.api.routes.job_responses import tracked_to_response
from acheron.shell.api.schemas import (
    AdminErrorResponse,
    ArchiveRequest,
    CleanupRequest,
    MarkFailedRequest,
    ReapStaleRequest,
    TokenRotateRequest,
)
from acheron.shell.retention import CleanupReport, RetentionPolicy
from acheron.shell.token_auth import RegistrationTokenAudit, RegistrationTokenStatus, TokenStoreError
from acheron.tls import CertificateError, CertificateStatus

if TYPE_CHECKING:
    from acheron.shell.orchestrator import Orchestrator

router = APIRouter()


def _admin_error(exc: AcheronError, *, status_code: int) -> HTTPException:
    safe = sanitise_exc_message(exc)
    _, separator, message = safe.partition(": ")
    error = AdminErrorResponse(
        type=type(exc).__name__,
        message=message if separator else safe,
        remediation=(sanitise_public_remediation(exc.remediation) if exc.remediation is not None else None),
    )
    return HTTPException(status_code=status_code, detail=error.model_dump())


def _not_implemented(action: str) -> HTTPException:
    error = AdminErrorResponse(
        type="AdminActionUnavailable",
        message=f"Administrative action {action!r} is not available",
        remediation="Use the supported administrative operation for this deployment.",
    )
    return HTTPException(status_code=501, detail=error.model_dump())


class _CertificateAdminManager(Protocol):
    def status(self) -> CertificateStatus | None:
        """Return the current certificate status."""
        ...

    def reload(self) -> CertificateStatus:
        """Reload the active certificate context."""
        ...


def _certificate_manager(request: Request) -> _CertificateAdminManager:
    manager = getattr(request.app.state, "certificate_manager", None)
    if manager is None:
        raise _admin_error(
            CertificateError(
                "TLS is not enabled",
                remediation="Configure ACHERON_TLS_CERT_FILE and ACHERON_TLS_KEY_FILE before retrying",
            ),
            status_code=503,
        )
    return cast("_CertificateAdminManager", manager)


def _certificate_response(status: CertificateStatus | None) -> CertificateStatusResponse:
    if status is None:
        return CertificateStatusResponse(enabled=False)
    return CertificateStatusResponse(
        enabled=True,
        name=status.name,
        subject=status.subject,
        expires_at=status.expires_at,
        remaining_seconds=status.remaining.total_seconds(),
        remaining_display=status.remaining_display,
        severity=status.severity,
    )


def _token_audit_response(audit: RegistrationTokenAudit) -> RegistrationTokenAuditResponse:
    match audit.result:
        case "created" | "success" | "failed" as result:
            typed_result: Literal["created", "success", "failed"] = result
        case _:
            typed_result = "failed"
    return RegistrationTokenAuditResponse(
        timestamp=audit.timestamp,
        reason=audit.reason,
        old_fingerprint=audit.old_fingerprint,
        new_fingerprint=audit.new_fingerprint,
        worker_ids=list(audit.worker_ids[:100]),
        result=typed_result,
        request_id=audit.request_id or None,
    )


def _token_status_response(orch: Orchestrator) -> RegistrationTokenStatusResponse:
    status: RegistrationTokenStatus = orch.registration_token_status()
    return RegistrationTokenStatusResponse(
        source=status.source,
        created_at=status.created_at,
        last_rotation_at=status.last_rotation_at,
        rotation_count=status.rotation_count,
        fingerprint=status.fingerprint,
        history=[_token_audit_response(audit) for audit in orch.registration_token_history()],
    )


@router.get("/token/status", response_model=RegistrationTokenStatusResponse)
async def token_status(
    request: Request,
    orch: OrchestratorDep,
    _token: AdminTokenDep,
) -> RegistrationTokenStatusResponse:
    """Return secret-free registration-token status and history."""

    async def operation() -> RegistrationTokenStatusResponse:
        try:
            return _token_status_response(orch)
        except TokenStoreError as exc:
            raise _admin_error(exc, status_code=503) from exc

    return await execute_admin_action(request, orch, operation)


@router.post("/token/rotate", response_model=RegistrationTokenRotationResponse)
async def token_rotate(
    body: TokenRotateRequest,
    request: Request,
    orch: OrchestratorDep,
    _token: AdminTokenDep,
) -> RegistrationTokenRotationResponse:
    """Rotate the file-backed registration token across worker edges."""

    async def operation() -> RegistrationTokenRotationResponse:
        try:
            status = await orch.rotate_registration_token(
                reason=body.reason,
                request_id=getattr(request.state, "request_id", ""),
            )
        except TokenStoreError as exc:
            raise _admin_error(exc, status_code=409) from exc
        history = orch.registration_token_history()
        latest = history[-1] if history else None
        rollout = RegistrationTokenRolloutResponse(
            success=True,
            worker_ids=list(latest.worker_ids[:100]) if latest is not None else [],
            message="Registration token rotated successfully",
        )
        return RegistrationTokenRotationResponse(
            rotated=True,
            status=RegistrationTokenStatusResponse(
                source=status.source,
                created_at=status.created_at,
                last_rotation_at=status.last_rotation_at,
                rotation_count=status.rotation_count,
                fingerprint=status.fingerprint,
                history=[_token_audit_response(audit) for audit in history],
            ),
            rollout=rollout,
        )

    return await execute_admin_action(
        request,
        orch,
        operation,
        details=AdminAuditDetails(reason=body.reason),
    )


@router.get("/certs/status", response_model=CertificateStatusResponse)
async def certificate_status(
    request: Request,
    orch: OrchestratorDep,
    _token: AdminTokenDep,
) -> CertificateStatusResponse:
    """Return sanitized certificate expiry metadata."""

    async def operation() -> CertificateStatusResponse:
        manager = _certificate_manager(request)
        try:
            return _certificate_response(manager.status())
        except CertificateError as exc:
            raise _admin_error(exc, status_code=503) from exc

    return await execute_admin_action(request, orch, operation)


@router.post("/certs/reload", response_model=CertificateReloadResponse)
async def reload_certificate(
    request: Request,
    orch: OrchestratorDep,
    _token: AdminTokenDep,
) -> CertificateReloadResponse:
    """Reload the active certificate context after validating its replacement."""

    async def operation() -> CertificateReloadResponse:
        manager = _certificate_manager(request)
        try:
            status = manager.reload()
        except CertificateError as exc:
            raise _admin_error(exc, status_code=422) from exc
        return CertificateReloadResponse(reloaded=True, certificate=_certificate_response(status))

    return await execute_admin_action(
        request,
        orch,
        operation,
        details=AdminAuditDetails(reason="certificate reload"),
    )


@router.post("/jobs/reap-stale", response_model=ReapStaleResponse)
async def reap_stale(
    body: ReapStaleRequest,
    request: Request,
    orch: OrchestratorDep,
    _token: AdminTokenDep,
) -> ReapStaleResponse:
    """Mark persisted running jobs stale after an operator-selected age."""

    async def operation() -> ReapStaleResponse:
        try:
            result = await orch.reap_stale_jobs(
                older_than_seconds=body.older_than_seconds,
                reason=body.reason,
            )
        except ValueError as exc:
            error = AdminErrorResponse(
                type="AdminRequestValidationError",
                message="Invalid stale-job reaping parameters",
                remediation="Provide a finite non-negative age and a reason.",
            )
            raise HTTPException(status_code=422, detail=error.model_dump()) from exc
        return ReapStaleResponse(reaped=len(result.job_ids), job_ids=list(result.job_ids[:1000]))

    return await execute_admin_action(
        request,
        orch,
        operation,
        details=lambda result: AdminAuditDetails(
            reason=body.reason,
            job_ids=tuple(result.job_ids[:1000]),
            affected_count=result.reaped,
        ),
    )


@router.post("/jobs/{job_id}/mark-failed", response_model=AdminJobResponse)
async def mark_failed(
    job_id: str,
    body: MarkFailedRequest,
    request: Request,
    orch: OrchestratorDep,
    _token: AdminTokenDep,
) -> AdminJobResponse:
    """Mark one non-active job failed with an operator reason."""

    async def operation() -> AdminJobResponse:
        try:
            tracked = await orch.mark_failed_by_admin(job_id, reason=body.reason)
        except AcheronError as exc:
            status_code = 404 if isinstance(exc, JobNotFoundError) else 409
            raise _admin_error(exc, status_code=status_code) from exc
        return AdminJobResponse(job=tracked_to_response(tracked))

    return await execute_admin_action(
        request,
        orch,
        operation,
        details=lambda result: AdminAuditDetails(
            reason=body.reason,
            job_ids=(result.job.job_id,),
            affected_count=1,
        ),
    )


@router.post("/jobs/{job_id}/archive", response_model=AdminJobResponse)
async def archive(
    job_id: str,
    body: ArchiveRequest,
    request: Request,
    orch: OrchestratorDep,
    _token: AdminTokenDep,
) -> AdminJobResponse:
    """Archive one terminal job without deleting its record or artifacts."""

    async def operation() -> AdminJobResponse:
        try:
            tracked = await orch.archive_job(job_id)
        except AcheronError as exc:
            status_code = 404 if isinstance(exc, JobNotFoundError) else 409
            raise _admin_error(exc, status_code=status_code) from exc
        return AdminJobResponse(job=tracked_to_response(tracked))

    return await execute_admin_action(
        request,
        orch,
        operation,
        details=lambda result: AdminAuditDetails(
            reason=body.reason,
            job_ids=(result.job.job_id,),
            affected_count=1,
        ),
    )


def _cleanup_response(report: CleanupReport) -> CleanupResponse:
    return CleanupResponse(
        apply=report.apply,
        candidates=[
            CleanupCandidateResponse(
                job_id=candidate.job_id,
                status=candidate.status.value,
                archived=candidate.archived,
                relative_paths=list(candidate.relative_paths[:1000]),
                reclaimable_bytes=candidate.reclaimable_bytes,
            )
            for candidate in report.candidates[:1000]
        ],
        deleted_job_ids=list(report.deleted_job_ids[:1000]),
        failures=[
            CleanupFailureResponse(
                job_id=failure.job_id,
                relative_paths=list(failure.relative_paths[:1000]),
                message=failure.message,
            )
            for failure in report.failures[:1000]
        ],
        deleted_count=report.deleted_count,
        deleted_bytes=report.deleted_bytes,
        reclaimable_bytes=report.reclaimable_bytes,
    )


@router.post("/cleanup", response_model=CleanupResponse)
async def cleanup(
    body: CleanupRequest,
    request: Request,
    orch: OrchestratorDep,
    _token: AdminTokenDep,
) -> CleanupResponse:
    """Preview or apply terminal-job retention cleanup."""

    async def operation() -> CleanupResponse:
        successful = body.keep_successful_seconds or body.retention_seconds
        failed = body.keep_failed_seconds or body.retention_seconds
        if successful is None or failed is None:
            raise ValueError("retention windows are required")
        policy = RetentionPolicy(timedelta(seconds=successful), timedelta(seconds=failed))
        report = await orch.apply_cleanup(policy) if body.apply else await orch.preview_cleanup(policy)
        return _cleanup_response(report)

    return await execute_admin_action(
        request,
        orch,
        operation,
        details=lambda result: AdminAuditDetails(
            reason=body.reason,
            job_ids=tuple(result.deleted_job_ids),
            affected_count=result.deleted_count,
        ),
        failure_reason=lambda result: "cleanup completed with per-job failures" if result.failures else None,
    )


@router.post("/jobs/cleanup")
async def cleanup_legacy(
    body: CleanupRequest,
    request: Request,
    orch: OrchestratorDep,
    _token: AdminTokenDep,
) -> None:
    """Reserve the retention-cleanup contract for the cleanup task."""

    async def operation() -> None:
        _ = body
        raise _not_implemented("cleanup")

    return await execute_admin_action(request, orch, operation)
