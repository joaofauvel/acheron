"""Job lifecycle route handlers."""

from __future__ import annotations

import fnmatch
from datetime import datetime  # noqa: TC003
from typing import Annotated

from fastapi import HTTPException, Query

from acheron.core.errors import (
    AcheronError,
    JobAlreadyRunningError,
    JobNotCancellableError,
    JobNotFoundError,
    sanitise_public_message,
)
from acheron.core.models import PlanStatus  # noqa: TC001
from acheron.core.schemas import JobListResponse, JobResponse
from acheron.shell.api.deps import OrchestratorDep, RegistrationTokenDep  # noqa: TC001
from acheron.shell.api.routes.job_responses import error_response, tracked_to_response
from acheron.shell.api.schemas import ResumeJobRequest  # noqa: TC001
from acheron.shell.job_store import JobQuery


async def get_job(job_id: str, orch: OrchestratorDep, _token: RegistrationTokenDep) -> JobResponse:
    """Get job status and result."""
    tracked = await orch.get_job(job_id)
    if tracked is None:
        raise HTTPException(status_code=404, detail=error_response(JobNotFoundError("Job not found")).model_dump())
    return tracked_to_response(tracked)


async def cancel_job(
    job_id: str,
    orch: OrchestratorDep,
    _token: RegistrationTokenDep,
) -> JobResponse:
    """Cancel an active job and return its persisted partial result."""
    try:
        tracked = await orch.cancel_job(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=error_response(exc).model_dump()) from exc
    except JobNotCancellableError as exc:
        raise HTTPException(status_code=409, detail=error_response(exc).model_dump()) from exc
    except AcheronError as exc:
        raise HTTPException(status_code=422, detail=error_response(exc).model_dump()) from exc
    return tracked_to_response(tracked)


async def resume_job(
    job_id: str,
    body: ResumeJobRequest,
    orch: OrchestratorDep,
    _token: RegistrationTokenDep,
) -> JobResponse:
    """Resume a saved job with selected cache invalidation."""
    try:
        tracked = await orch.resume_job(
            job_id,
            invalidate_steps=body.invalidate_steps,
            invalidate_chapters=body.invalidate_chapters,
        )
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=error_response(exc).model_dump()) from exc
    except JobAlreadyRunningError as exc:
        raise HTTPException(status_code=409, detail=error_response(exc).model_dump()) from exc
    except AcheronError as exc:
        raise HTTPException(status_code=422, detail=error_response(exc).model_dump()) from exc
    return tracked_to_response(tracked)


async def list_jobs(  # noqa: PLR0913
    orch: OrchestratorDep,
    _token: RegistrationTokenDep,
    label: Annotated[str | None, Query()] = None,
    status: Annotated[PlanStatus | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    before: Annotated[datetime | None, Query()] = None,
    older_than_seconds: Annotated[float | None, Query(ge=0)] = None,
    include_archived: Annotated[bool, Query()] = False,  # noqa: FBT002
) -> JobListResponse:
    """List jobs using typed lifecycle filters and an optional label glob."""
    try:
        query = JobQuery(
            status=status,
            since=since,
            before=before,
            older_than_seconds=older_than_seconds,
            include_archived=include_archived,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=sanitise_public_message(str(exc))) from exc
    jobs = await orch.list_jobs(query)
    if label is not None:
        jobs = tuple(job for job in jobs if fnmatch.fnmatchcase(job.label or "", label))
    return JobListResponse(jobs=[tracked_to_response(job) for job in jobs[:1000]])
