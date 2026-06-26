"""Job submission and status routes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException

from acheron.core.errors import AcheronError, JobAlreadyRunningError, JobNotFoundError
from acheron.core.models import AudioRequest, EpubRequest, ExecutorStrategy
from acheron.shell.api.deps import OrchestratorDep, RegistrationTokenDep  # noqa: TC001
from acheron.shell.api.schemas import JobListResponse, JobResponse, SubmitJobRequest

if TYPE_CHECKING:
    from acheron.shell.job_store import TrackedJob

router = APIRouter()


@router.post("", status_code=201, response_model=JobResponse)
async def submit_job(
    body: SubmitJobRequest,
    orch: OrchestratorDep,
    _token: RegistrationTokenDep,
) -> JobResponse:
    """Submit a new job for processing."""
    try:
        strategy = ExecutorStrategy(body.executor_strategy)
    except ValueError as exc:
        msg = f"Invalid strategy: {body.executor_strategy}"
        raise HTTPException(status_code=400, detail=msg) from exc

    job_request: EpubRequest | AudioRequest
    match body.source_type:
        case "epub":
            job_request = EpubRequest(
                source_path=body.source_path,
                source_language=body.source_language,
                target_language=body.target_language,
            )
        case "audio":
            job_request = AudioRequest(
                source_path=body.source_path,
                source_language=body.source_language,
                target_language=body.target_language,
                asr_model=body.asr_model,
            )
        case _:
            msg = f"Invalid source_type: {body.source_type}"
            raise HTTPException(status_code=400, detail=msg)

    try:
        tracked = await orch.submit_job(job_request, strategy)
    except AcheronError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return _tracked_to_response(tracked)


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: str, orch: OrchestratorDep) -> JobResponse:
    """Get job status and result."""
    tracked = await orch.get_job(job_id)
    if tracked is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    return _tracked_to_response(tracked)


@router.post("/{job_id}/resume", response_model=JobResponse)
async def resume_job(
    job_id: str,
    orch: OrchestratorDep,
    _token: RegistrationTokenDep,
    force_fresh: bool = False,  # noqa: FBT001, FBT002
) -> JobResponse:
    """Resume a saved job."""
    try:
        tracked = await orch.resume_job(job_id, force_fresh=force_fresh)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobAlreadyRunningError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AcheronError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _tracked_to_response(tracked)


@router.get("", response_model=JobListResponse)
async def list_jobs(orch: OrchestratorDep) -> JobListResponse:
    """List all jobs."""
    jobs = await orch.list_jobs()
    return JobListResponse(jobs=[_tracked_to_response(j) for j in jobs])


def _tracked_to_response(tracked: TrackedJob) -> JobResponse:
    result = tracked.result
    return JobResponse(
        job_id=tracked.job_id,
        status=tracked.status,
        plan_id=tracked.plan.plan_id if tracked.plan else None,
        completed_steps=result.completed_steps if result else 0,
        total_steps=result.total_steps if result else 0,
        total_cost=result.total_cost if result else 0.0,
        total_duration_seconds=result.total_duration_seconds if result else 0.0,
        total_cost_basis=(result.total_cost_basis if result and result.total_cost_basis else None),
        errors=list(result.errors) if result else [],
    )
