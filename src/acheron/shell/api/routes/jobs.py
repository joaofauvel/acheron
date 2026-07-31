"""Job submission and status routes."""

from __future__ import annotations

import fnmatch
import logging
import math
import time
from datetime import datetime  # noqa: TC003
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, HTTPException, Query
from starlette.responses import StreamingResponse

from acheron.core.errors import (
    AcheronError,
    JobAlreadyRunningError,
    JobNotCancellableError,
    JobNotFoundError,
    sanitise_exc_message,
)
from acheron.core.models import (
    AudioRequest,
    EpubRequest,
    ExecutorStrategy,
    JobRequest,
    PlanStatus,
    StepError as DomainStepError,
    WorkerStatus,
    WorkerType,
)
from acheron.core.schemas import (
    ErrorResponse,
    JobListResponse,
    JobLogEvent,
    JobProgress,
    JobResponse,
    OutputSummary,
    PlanResponse,
    StepError as StepErrorResponse,
)
from acheron.shell.api.deps import OrchestratorDep, RegistrationTokenDep  # noqa: TC001
from acheron.shell.api.schemas import ResumeJobRequest, RetryJobRequest, SubmitJobRequest  # noqa: TC001
from acheron.shell.input_store import InputPathError, InputStore
from acheron.shell.job_store import JobQuery

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from acheron.shell.job_store import TrackedJob
    from acheron.shell.orchestrator import Orchestrator
    from acheron.shell.registry import RegisteredWorker

logger = logging.getLogger(__name__)
router = APIRouter()


def _error_response(exc: AcheronError) -> ErrorResponse:
    safe = sanitise_exc_message(exc)
    _, separator, message = safe.partition(": ")
    return ErrorResponse(
        type=type(exc).__name__,
        message=message if separator else safe,
        remediation=exc.remediation,
    )


def _resolve_submission_source(orch: Orchestrator, source_path: str) -> Path:
    """Resolve a user-supplied relative source path to an allowlisted regular file.

    Rejects invalid paths with stable public messages while logging path details.
    """
    data_dir = orch.settings.orchestrator.data_dir
    if not source_path or Path(source_path).is_absolute():
        logger.warning("Rejected source path %r (data directory %s)", source_path, data_dir)
        msg = "Invalid source_path: provide a non-empty relative path"
        raise HTTPException(status_code=422, detail=msg)
    store = InputStore(data_dir)
    try:
        return store.resolve_source_path(source_path)
    except InputPathError as exc:
        try:
            (data_dir / source_path).resolve().relative_to(data_dir.resolve())
        except ValueError:
            logger.warning("Rejected source path %r outside data directory %s", source_path, data_dir)
            msg = "Invalid source_path: must resolve to a regular file under the configured input directory"
            raise HTTPException(status_code=422, detail=msg) from exc
        logger.warning("Source path %r was not readable under data directory %s", source_path, data_dir)
        msg = "Invalid source_path: source file is unavailable"
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
            source_identity = _submission_source_identity(orch, body.source_path)
            job_request = EpubRequest(
                source_path=source_identity,
                source_language=body.source_language,
                target_language=body.target_language,
            )
        case "audio":
            if not normalized_asr_model:
                msg = "asr_model is required for source_type='audio'"
                raise HTTPException(status_code=422, detail=msg)
            source_identity = _submission_source_identity(orch, body.source_path)
            job_request = AudioRequest(
                source_path=source_identity,
                source_language=body.source_language,
                target_language=body.target_language,
                asr_model=normalized_asr_model,
            )
        case _:
            msg = f"Invalid source_type: {body.source_type}"
            raise HTTPException(status_code=400, detail=msg)
    return job_request, strategy


def _submission_source_identity(orch: Orchestrator, source_path: str) -> str:
    """Validate a source and return its canonical data-directory-relative identity."""
    resolved = _resolve_submission_source(orch, source_path)
    return InputStore(orch.settings.orchestrator.data_dir, create=False).normalize_source_path(str(resolved))


def _resolve_stored_source(orch: Orchestrator, source_path: str) -> str:
    """Revalidate a stored source and return its canonical identity."""
    candidate = Path(source_path)
    data_dir = orch.settings.orchestrator.data_dir
    if candidate.is_absolute():
        try:
            relative_path = candidate.resolve(strict=False).relative_to(data_dir.resolve())
        except ValueError as exc:
            logger.warning("Stored source path %r is outside data directory %s", source_path, data_dir)
            msg = "Invalid stored source_path"
            raise HTTPException(status_code=422, detail=msg) from exc
    else:
        relative_path = candidate
    return _submission_source_identity(orch, str(relative_path))


async def _build_retry_request(
    orch: Orchestrator,
    source: TrackedJob,
    body: RetryJobRequest,
) -> tuple[JobRequest, ExecutorStrategy, str | None]:
    """Merge retry overrides into the stored request and validate strategy."""
    strategy_value = body.executor_strategy if body.executor_strategy is not None else source.strategy.value
    if "asr_model" in body.model_fields_set and body.asr_model is not None and not body.asr_model.strip():
        msg = "asr_model override must not be empty"
        raise HTTPException(status_code=422, detail=msg)
    try:
        strategy = ExecutorStrategy(strategy_value)
    except ValueError as exc:
        msg = f"Invalid strategy: {strategy_value}"
        raise HTTPException(status_code=400, detail=msg) from exc

    label = body.label if body.label is not None else source.label
    match source.request:
        case EpubRequest(source_path=source_path, source_language=source_language, target_language=target_language):
            if body.source_path is not None:
                path = _submission_source_identity(orch, body.source_path)
            else:
                path = _resolve_stored_source(orch, source_path)
            if body.asr_model is not None and body.asr_model.strip():
                msg = "asr_model is only valid for source_type='audio'"
                raise HTTPException(status_code=422, detail=msg)
            request: JobRequest = EpubRequest(
                source_path=path,
                source_language=body.source_language if body.source_language is not None else source_language,
                target_language=body.target_language if body.target_language is not None else target_language,
            )
        case AudioRequest(
            source_path=source_path,
            source_language=source_language,
            target_language=target_language,
            asr_model=asr_model,
        ):
            if body.source_path is not None:
                path = _submission_source_identity(orch, body.source_path)
            else:
                path = _resolve_stored_source(orch, source_path)
            selected_asr_model = body.asr_model.strip() if body.asr_model is not None else asr_model
            if not selected_asr_model:
                msg = "asr_model is required for source_type='audio'"
                raise HTTPException(status_code=422, detail=msg)
            request = AudioRequest(
                source_path=path,
                source_language=body.source_language if body.source_language is not None else source_language,
                target_language=body.target_language if body.target_language is not None else target_language,
                asr_model=selected_asr_model,
            )
    return request, strategy, label


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
        raise HTTPException(status_code=422, detail=_error_response(exc).model_dump()) from exc

    warnings: list[str] = []
    try:
        warnings = _booting_tts_warnings(await orch.list_workers(), now=time.time())
    except Exception:
        logger.exception("Failed to inspect workers for job submission warnings")
    return _tracked_to_response(tracked, warnings=warnings)


@router.post("/{job_id}/retry", response_model=JobResponse)
async def retry_job(
    job_id: str,
    body: RetryJobRequest,
    orch: OrchestratorDep,
    _token: RegistrationTokenDep,
) -> JobResponse:
    """Create a fresh job from a stored submission with optional overrides."""
    source = await orch.get_job(job_id)
    if source is None:
        exc = JobNotFoundError(f"Job not found: {job_id}")
        raise HTTPException(status_code=404, detail=_error_response(exc).model_dump()) from exc
    request, strategy, label = await _build_retry_request(orch, source, body)
    try:
        tracked = await orch.submit_retry(job_id, request, strategy, label=label)
    except AcheronError as exc:
        raise HTTPException(status_code=422, detail=_error_response(exc).model_dump()) from exc
    return _tracked_to_response(tracked)


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
        raise HTTPException(status_code=422, detail=_error_response(exc).model_dump()) from exc
    return PlanResponse.from_plan(plan)


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: str, orch: OrchestratorDep) -> JobResponse:
    """Get job status and result."""
    tracked = await orch.get_job(job_id)
    if tracked is None:
        raise HTTPException(
            status_code=404, detail=_error_response(JobNotFoundError(f"Job not found: {job_id}")).model_dump()
        )
    return _tracked_to_response(tracked)


@router.post("/{job_id}/cancel", response_model=JobResponse)
async def cancel_job(
    job_id: str,
    orch: OrchestratorDep,
    _token: RegistrationTokenDep,
) -> JobResponse:
    """Cancel an active job and return its persisted partial result."""
    try:
        tracked = await orch.cancel_job(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=_error_response(exc).model_dump(),
        ) from exc
    except JobNotCancellableError as exc:
        raise HTTPException(
            status_code=409,
            detail=_error_response(exc).model_dump(),
        ) from exc
    except AcheronError as exc:
        raise HTTPException(
            status_code=422,
            detail=_error_response(exc).model_dump(),
        ) from exc
    return _tracked_to_response(tracked)


@router.get("/{job_id}/logs")
async def job_logs(
    job_id: str,
    orch: OrchestratorDep,
    *,
    follow: Annotated[bool, Query()] = True,
) -> StreamingResponse:
    """Stream job progress events as newline-delimited JSON."""
    from acheron.shell.job_events import iter_events  # noqa: PLC0415

    tracked = await orch.get_job(job_id)
    if tracked is None:
        raise HTTPException(
            status_code=404,
            detail=_error_response(JobNotFoundError(f"Job not found: {job_id}")).model_dump(),
        )

    _terminal = {PlanStatus.COMPLETED, PlanStatus.FAILED, PlanStatus.PARTIAL}
    if tracked.status in _terminal or not follow:
        # Job already terminal or caller only wants snapshot: emit buffered
        # snapshot directly without subscribing (avoids hang on terminal jobs).
        snapshot = JobLogEvent(
            job_id=tracked.job_id,
            timestamp=tracked.last_persisted_at or tracked.created_at,
            status=tracked.status,
            step_id=tracked.progress.current_step_id,
            worker_type=tracked.progress.current_worker_type,
            worker_id=tracked.progress.current_worker_id,
            progress=JobProgress(
                completed_steps=tracked.progress.completed_steps,
                total_steps=tracked.progress.total_steps,
                current_step_id=tracked.progress.current_step_id,
                current_worker_type=tracked.progress.current_worker_type,
                current_worker_id=tracked.progress.current_worker_id,
                eta_seconds=tracked.progress.eta_seconds,
            ),
            message=f"job {tracked.status.value}",
        )
        return StreamingResponse(
            iter([snapshot.model_dump_json().encode() + b"\n"]),
            media_type="application/x-ndjson",
        )

    queue = await orch.events.subscribe(job_id)

    async def _stream() -> AsyncIterator[bytes]:
        async for event in iter_events(queue):
            yield event.model_dump_json().encode() + b"\n"

    return StreamingResponse(_stream(), media_type="application/x-ndjson")


@router.post("/{job_id}/resume", response_model=JobResponse)
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
        raise HTTPException(status_code=404, detail=_error_response(exc).model_dump()) from exc
    except JobAlreadyRunningError as exc:
        raise HTTPException(status_code=409, detail=_error_response(exc).model_dump()) from exc
    except AcheronError as exc:
        raise HTTPException(status_code=422, detail=_error_response(exc).model_dump()) from exc
    return _tracked_to_response(tracked)


@router.get("", response_model=JobListResponse)
async def list_jobs(  # noqa: PLR0913
    orch: OrchestratorDep,
    label: Annotated[str | None, Query()] = None,
    status: Annotated[PlanStatus | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    before: Annotated[datetime | None, Query()] = None,
    older_than_seconds: Annotated[float | None, Query(ge=0)] = None,
    include_archived: Annotated[bool, Query()] = False,  # noqa: FBT002
) -> JobListResponse:
    """List jobs using typed lifecycle filters and an optional label glob."""
    query = JobQuery(
        status=status,
        since=since,
        before=before,
        older_than_seconds=older_than_seconds,
        include_archived=include_archived,
    )
    jobs = await orch.list_jobs(query)
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
        archived_at=tracked.archived_at,
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
                    download_url=f"/jobs/{tracked.job_id}/outputs/{index}",
                    filename=output.filename,
                    size_bytes=output.size_bytes,
                    content_type=output.content_type,
                )
                for index, output in enumerate(result.outputs)
            ]
            if result
            else []
        ),
        errors=([_to_step_error_response(error) for error in result.errors] if result else []),
        warnings=warnings if warnings is not None else [],
    )
