"""Operator-only job recovery and archive routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from acheron.core.errors import AcheronError, JobNotFoundError, sanitise_exc_message
from acheron.core.schemas import AdminJobResponse, ReapStaleResponse
from acheron.shell.api.admin_audit import AdminAuditDetails, execute_admin_action
from acheron.shell.api.deps import AdminTokenDep, OrchestratorDep  # noqa: TC001
from acheron.shell.api.routes.jobs import _tracked_to_response
from acheron.shell.api.schemas import (
    AdminErrorResponse,
    ArchiveRequest,
    CleanupRequest,
    MarkFailedRequest,
    ReapStaleRequest,
)

router = APIRouter()


def _admin_error(exc: AcheronError, *, status_code: int) -> HTTPException:
    safe = sanitise_exc_message(exc)
    _, separator, message = safe.partition(": ")
    error = AdminErrorResponse(
        type=type(exc).__name__,
        message=message if separator else safe,
        remediation=exc.remediation,
    )
    return HTTPException(status_code=status_code, detail=error.model_dump())


def _not_implemented(action: str) -> HTTPException:
    error = AdminErrorResponse(
        type="AdminActionUnavailable",
        message=f"Administrative action {action!r} is not available",
        remediation="Use the supported administrative operation for this deployment.",
    )
    return HTTPException(status_code=501, detail=error.model_dump())


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
        return ReapStaleResponse(reaped=len(result.job_ids), job_ids=list(result.job_ids))

    return await execute_admin_action(
        request,
        orch,
        operation,
        details=lambda result: AdminAuditDetails(
            reason=body.reason,
            job_ids=tuple(result.job_ids),
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
        return AdminJobResponse(job=_tracked_to_response(tracked))

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
        return AdminJobResponse(job=_tracked_to_response(tracked))

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


@router.post("/jobs/cleanup")
async def cleanup(
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
