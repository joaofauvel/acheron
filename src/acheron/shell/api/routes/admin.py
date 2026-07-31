"""Administrative route contracts and authorization seams."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from acheron.core.errors import JobNotFoundError
from acheron.shell.api.admin_audit import execute_admin_action
from acheron.shell.api.deps import AdminTokenDep, OrchestratorDep  # noqa: TC001
from acheron.shell.api.schemas import (
    AdminErrorResponse,
    ArchiveRequest,
    CleanupRequest,
    MarkFailedRequest,
    ReapStaleRequest,
)

router = APIRouter()


def _not_implemented(action: str) -> HTTPException:
    error = AdminErrorResponse(
        type="AdminActionUnavailable",
        message=f"Administrative action {action!r} is not available",
        remediation="Use the supported administrative operation for this deployment.",
    )
    return HTTPException(status_code=501, detail=error.model_dump())


@router.post("/jobs/reap-stale")
async def reap_stale(
    body: ReapStaleRequest,
    request: Request,
    orch: OrchestratorDep,
    _token: AdminTokenDep,
) -> None:
    """Reserve the stale-job reaping request contract."""

    async def operation() -> None:
        _ = body
        raise _not_implemented("reap-stale")

    return await execute_admin_action(request, orch, operation)


@router.post("/jobs/{job_id}/mark-failed")
async def mark_failed(
    job_id: str,
    body: MarkFailedRequest,
    request: Request,
    orch: OrchestratorDep,
    _token: AdminTokenDep,
) -> None:
    """Reserve the mark-failed request contract."""

    async def operation() -> None:
        _ = body
        if await orch.get_job(job_id) is None:
            error = AdminErrorResponse(
                type=JobNotFoundError.__name__,
                message=f"Job not found: {job_id}",
                remediation="Verify the job ID and retry.",
            )
            raise HTTPException(status_code=404, detail=error.model_dump())
        raise _not_implemented("mark-failed")

    return await execute_admin_action(request, orch, operation)


@router.post("/jobs/{job_id}/archive")
async def archive(
    job_id: str,
    body: ArchiveRequest,
    request: Request,
    orch: OrchestratorDep,
    _token: AdminTokenDep,
) -> None:
    """Reserve the archive request contract."""

    async def operation() -> None:
        _ = body
        if await orch.get_job(job_id) is None:
            error = AdminErrorResponse(
                type=JobNotFoundError.__name__,
                message=f"Job not found: {job_id}",
                remediation="Verify the job ID and retry.",
            )
            raise HTTPException(status_code=404, detail=error.model_dump())
        raise _not_implemented("archive")

    return await execute_admin_action(request, orch, operation)


@router.post("/jobs/cleanup")
async def cleanup(
    body: CleanupRequest,
    request: Request,
    orch: OrchestratorDep,
    _token: AdminTokenDep,
) -> None:
    """Reserve the cleanup request contract."""

    async def operation() -> None:
        _ = body
        raise _not_implemented("cleanup")

    return await execute_admin_action(request, orch, operation)
