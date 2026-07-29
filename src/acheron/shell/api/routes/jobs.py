"""Job submission and status routes."""

from __future__ import annotations

import fnmatch
import logging
import math
import time
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, HTTPException, Query

from acheron.core.errors import (
    AcheronError,
    JobAlreadyRunningError,
    JobNotFoundError,
    sanitise_exc_message,
)
from acheron.core.models import (
    AudioRequest,
    EpubRequest,
    ExecutorStrategy,
    JobRequest,
    StepError as DomainStepError,
    WorkerStatus,
    WorkerType,
)
from acheron.core.schemas import (
    JobListResponse,
    JobProgress,
    JobResponse,
    OutputSummary,
    PlanResponse,
    StepError as StepErrorResponse,
)
from acheron.shell.api.deps import OrchestratorDep, RegistrationTokenDep  # noqa: TC001
from acheron.shell.api.schemas import SubmitJobRequest  # noqa: TC001
from acheron.shell.input_store import InputPathError, InputStore

if TYPE_CHECKING:
    from acheron.shell.job_store import TrackedJob
    from acheron.shell.orchestrator import Orchestrator
    from acheron.shell.registry import RegisteredWorker

logger = logging.getLogger(__name__)
router = APIRouter()


def _resolve_submission_source(orch: Orchestrator, source_path: str) -> Path:
    """Resolve a user-supplied relative source path to an allowlisted regular file.

    Distinguishes two failure modes for the caller to render in HTTP 422 details:
    - ``relative-path error`` for empty, absolute, or traversal paths.
    - ``source_path not found: <requested>; expected at <data_dir>/<requested>``
      when the path resolves inside the data directory but is missing or
      not a regular file.
    """
    data_dir = orch.settings.orchestrator.data_dir
    if not source_path or Path(source_path).is_absolute():
        msg = f"Invalid source path {source_path!r}: must be a non-empty relative path under {data_dir}"
        raise HTTPException(status_code=422, detail=msg)
    store = InputStore(data_dir)
    try:
        return store.resolve_source_path(source_path)
    except InputPathError as exc:
        try:
            (data_dir / source_path).resolve().relative_to(data_dir.resolve())
        except ValueError:
            msg = f"Invalid source path {source_path!r}: must resolve to a regular file under {data_dir}"
            raise HTTPException(status_code=422, detail=msg) from exc
        msg = f"source_path not found: {source_path}; expected at {data_dir}/{source_path}"
        raise HTTPException(status_code=422, detail=msg) from exc


async def _build_job_request(
    orch: Orchestrator,
    body: SubmitJobRequest,
) -> tuple[JobRequest, ExecutorStrategy]:
    """Validate a submission body and resolve its source path.

    Shared by ``POST /jobs`` and ``POST /jobs:preview`` so the two endpoints
    cannot drift in their preflight behaviour. Returns the typed
    :class:`JobRequest` and parsed :class:`ExecutorStrategy` for the caller
    to forward into the orchestrator.
    """
    try:
        strategy = ExecutorStrategy(body.executor_strategy)
    except ValueError as exc:
        msg = f"Invalid strategy: {body.executor_strategy}"
        raise HTTPException(status_code=400, detail=msg) from exc

    normalized_asr_model = body.asr_model.strip() if body.asr_model is not None else None
    job_request: JobRequest
    match body.source_type:
        case "epub":
            if normalized_asr_model is not None:
                msg = "asr_model is only valid for source_type='audio'"
                raise HTTPException(status_code=422, detail=msg)
            resolved_source = _resolve_submission_source(orch, body.source_path)
            job_request = EpubRequest(
                source_path=str(resolved_source),
                source_language=body.source_language,
                target_language=body.target_language,
            )
        case "audio":
            if not normalized_asr_model:
                msg = "asr_model is required for source_type='audio'"
                raise HTTPException(status_code=422, detail=msg)
            resolved_source = _resolve_submission_source(orch, body.source_path)
            job_request = AudioRequest(
                source_path=str(resolved_source),
                source_language=body.source_language,
                target_language=body.target_language,
                asr_model=normalized_asr_model,
            )
        case _:
            msg = f"Invalid source_type: {body.source_type}"
            raise HTTPException(status_code=400, detail=msg)
    return job_request, strategy


@router.post("", status_code=201, response_model=JobResponse)
async def submit_job(
    body: SubmitJobRequest,
    orch: OrchestratorDep,
    _token: RegistrationTokenDep,
) -> JobResponse:
    """Submit a new job for processing."""
    job_request, strategy = await _build_job_request(orch, body)
    try:
        if body.label is None:
            tracked = await orch.submit_job(job_request, strategy)
        else:
            tracked = await orch.submit_job(job_request, strategy, label=body.label)
    except AcheronError as exc:
        raise HTTPException(status_code=422, detail=sanitise_exc_message(exc)) from exc

    warnings: list[str] = []
    try:
        warnings = _booting_tts_warnings(await orch.list_workers(), now=time.time())
    except Exception:
        logger.exception("Failed to inspect workers for job submission warnings")
    return _tracked_to_response(tracked, warnings=warnings)


@router.post(":preview", response_model=PlanResponse)
async def preview_job(
    body: SubmitJobRequest,
    orch: OrchestratorDep,
    _token: RegistrationTokenDep,
) -> PlanResponse:
    """Compile a plan without persisting a job or scheduling execution.

    Reuses :func:`_build_job_request` so the same preflight that gates
    ``POST /jobs`` also gates the preview endpoint — operators see exactly
    the validation a real submit would experience.
    """
    job_request, strategy = await _build_job_request(orch, body)
    try:
        plan = await orch.preview_job(job_request, strategy)
    except AcheronError as exc:
        raise HTTPException(status_code=422, detail=sanitise_exc_message(exc)) from exc
    return PlanResponse.from_plan(plan)


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
        raise HTTPException(status_code=404, detail=sanitise_exc_message(exc)) from exc
    except JobAlreadyRunningError as exc:
        raise HTTPException(status_code=400, detail=sanitise_exc_message(exc)) from exc
    except AcheronError as exc:
        raise HTTPException(status_code=422, detail=sanitise_exc_message(exc)) from exc
    return _tracked_to_response(tracked)


@router.get("", response_model=JobListResponse)
async def list_jobs(
    orch: OrchestratorDep,
    label: Annotated[str | None, Query()] = None,
) -> JobListResponse:
    """List jobs, optionally filtered by label glob."""
    jobs = await orch.list_jobs()
    if label is not None:
        jobs = tuple(job for job in jobs if fnmatch.fnmatchcase(job.label or "", label))
    return JobListResponse(jobs=[_tracked_to_response(j) for j in jobs])


def _booting_tts_warnings(
    workers: tuple[RegisteredWorker, ...],
    *,
    now: float,
) -> list[str]:
    """Build an informational warning for BOOTING TTS workers."""
    affected = sorted(
        (
            worker
            for worker in workers
            if worker.capabilities.worker_type is WorkerType.TTS
            and worker.status is WorkerStatus.BOOTING
            and worker.booting_since is not None
        ),
        key=lambda worker: worker.worker_id,
    )
    if not affected:
        return []
    elapsed = ", ".join(
        f"{worker.worker_id} ({math.floor(max(0.0, now - (worker.booting_since or 0.0)))}s elapsed)"
        for worker in affected
    )
    return [f"BOOTING TTS workers: {elapsed}; cold start typically takes 30\u201390 seconds."]


def _to_step_error_response(error: DomainStepError) -> StepErrorResponse:
    return StepErrorResponse(
        step_id=error.step_id,
        worker_type=error.worker_type,
        worker_id=error.worker_id,
        message=error.message,
        timestamp=error.timestamp,
    )


def _tracked_to_response(tracked: TrackedJob, warnings: list[str] | None = None) -> JobResponse:
    result = tracked.result
    match tracked.request:
        case AudioRequest(
            source_language=source_language,
            target_language=target_language,
            asr_model=asr_model,
        ):
            source_type = "audio"
        case EpubRequest(
            source_language=source_language,
            target_language=target_language,
        ):
            source_type = "epub"
            asr_model = None

    progress = tracked.progress
    return JobResponse(
        job_id=tracked.job_id,
        status=tracked.status,
        plan_id=tracked.plan.plan_id if tracked.plan else None,
        label=tracked.label,
        retries_from=tracked.retries_from,
        source_type=source_type,
        source_language=source_language,
        target_language=target_language,
        asr_model=asr_model,
        executor_strategy=tracked.strategy,
        created_at=tracked.created_at,
        last_persisted_at=tracked.last_persisted_at,
        progress=JobProgress(
            completed_steps=progress.completed_steps,
            total_steps=progress.total_steps,
            current_step_id=progress.current_step_id,
            current_worker_type=progress.current_worker_type,
            current_worker_id=progress.current_worker_id,
            eta_seconds=progress.eta_seconds,
        ),
        total_cost=result.total_cost if result else 0.0,
        total_duration_seconds=result.total_duration_seconds if result else 0.0,
        total_cost_basis=(result.total_cost_basis if result and result.total_cost_basis else None),
        outputs=(
            [
                OutputSummary(
                    path=output.path,
                    filename=output.filename,
                    size_bytes=output.size_bytes,
                    content_type=output.content_type,
                )
                for output in result.outputs
            ]
            if result
            else []
        ),
        errors=([_to_step_error_response(error) for error in result.errors] if result else []),
        warnings=warnings if warnings is not None else [],
    )
