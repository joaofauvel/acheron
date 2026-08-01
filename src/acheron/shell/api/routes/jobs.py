"""Job submission and status routes."""

from __future__ import annotations

import fnmatch
import logging
import math
import re
import time
import zipfile
from datetime import datetime  # noqa: TC003
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, HTTPException, Query
from starlette.responses import StreamingResponse

from acheron.core.epub import read_epub_chapters
from acheron.core.errors import (
    AcheronError,
    JobAlreadyRunningError,
    JobNotCancellableError,
    JobNotFoundError,
    sanitise_public_message,
    sanitise_public_remediation,
)
from acheron.core.models import (
    AudioRequest,
    EpubRequest,
    ExecutorStrategy,
    JobRequest,
    PlanStatus,
    StepError as DomainStepError,
    VoiceRange,
    VoiceSelection,
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
from acheron.shell.api.public import public_content_type, public_optional_worker_id, public_worker_id
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


_INPUT_ID_RE = re.compile(r"[0-9a-f]{32}")
_INPUT_SOURCE_RE = re.compile(r"^inputs/([0-9a-f]{32})/.+$")


async def _cleanup_temporary_inputs(orch: Orchestrator, input_ids: set[str]) -> None:
    """Best-effort cleanup for failed temporary-input submissions."""
    for input_id in sorted(input_ids):
        try:
            await orch.delete_input(input_id)
        except (InputPathError, OSError) as exc:
            logger.warning("Temporary input cleanup failed for %s: %s", input_id, exc)
        except Exception:
            logger.exception("Temporary input cleanup failed for %s", input_id)


def _temporary_input_ids(body: SubmitJobRequest) -> set[str]:
    """Extract only canonical temporary-input identities from a request."""
    identities: set[str] = set()
    if body.input_id is not None and _INPUT_ID_RE.fullmatch(body.input_id):
        identities.add(body.input_id)
    source_match = _INPUT_SOURCE_RE.fullmatch(body.source_path)
    if source_match is not None:
        identities.add(source_match.group(1))
    return identities


def _error_response(exc: AcheronError) -> ErrorResponse:
    return ErrorResponse(
        type=type(exc).__name__,
        message=sanitise_public_message(str(exc)),
        remediation=(sanitise_public_remediation(exc.remediation) if exc.remediation is not None else None),
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
    try:
        store = InputStore(data_dir)
        return store.resolve_source_path(source_path)
    except OSError as exc:
        logger.warning("Source path %r could not be resolved under %s: %s", source_path, data_dir, exc)
        raise HTTPException(status_code=422, detail="Invalid source_path: source file is unavailable") from exc
    except InputPathError as exc:
        try:
            (data_dir / source_path).resolve().relative_to(data_dir.resolve())
        except ValueError:
            logger.warning("Rejected source path %r outside data directory %s", source_path, data_dir)
            msg = "Invalid source_path: must resolve to a regular file under the configured input directory"
            raise HTTPException(status_code=422, detail=msg) from exc
        except OSError as resolve_exc:
            logger.warning("Source path %r could not be inspected under %s: %s", source_path, data_dir, resolve_exc)
            raise HTTPException(status_code=422, detail="Invalid source_path: source file is unavailable") from exc
        logger.warning("Source path %r was not readable under data directory %s", source_path, data_dir)
        msg = "Invalid source_path: source file is unavailable"
        raise HTTPException(status_code=422, detail=msg) from exc


def _voice_ranges(body: SubmitJobRequest) -> tuple[VoiceRange, ...]:
    """Convert strict wire ranges into canonical domain values."""
    try:
        return tuple(VoiceRange(item.start_chapter, item.end_chapter, item.voice) for item in body.voice_map)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=sanitise_public_message(str(exc))) from exc


async def _canonicalize_voices(
    orch: Orchestrator,
    target_language: str,
    default_voice: str | None,
    ranges: tuple[VoiceRange, ...],
) -> tuple[str | None, tuple[VoiceRange, ...]]:
    """Resolve case-insensitive user names to worker-advertised spellings."""
    names: dict[str, str] = {}
    for worker in await orch.list_workers():
        capabilities = worker.capabilities
        if capabilities.worker_type is not WorkerType.TTS:
            continue
        if (
            target_language not in capabilities.supported_languages_in
            or target_language not in capabilities.supported_languages_out
        ):
            continue
        value = capabilities.metadata.get("speakers")
        if isinstance(value, list):
            names.update({item.casefold(): item for item in value if isinstance(item, str) and item.strip()})

    def canonical(value: str | None) -> str | None:
        return names.get(value.casefold(), value) if value is not None else None

    return canonical(default_voice), tuple(
        VoiceRange(item.start_chapter, item.end_chapter, canonical(item.voice) or item.voice) for item in ranges
    )


def _validate_voice_selection(
    source_path: Path,
    default_voice: str | None,
    ranges: tuple[VoiceRange, ...],
) -> tuple[VoiceRange, ...]:
    """Validate an EPUB voice map against its discovered chapter count."""
    if not ranges:
        return ()
    try:
        chapter_count = len(read_epub_chapters(Path(source_path)))
        return VoiceSelection.from_ranges(default_voice, ranges, chapter_count).ranges
    except (OSError, UnicodeError, zipfile.BadZipFile) as exc:
        logger.warning("Unable to inspect EPUB chapters for voice selection at %s: %s", source_path, exc)
        raise HTTPException(status_code=422, detail="unable to inspect EPUB chapters") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid voice selection") from exc


async def _build_job_request(  # noqa: C901, PLR0912
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
        raise HTTPException(status_code=400, detail="Invalid executor strategy") from exc

    normalized_asr_model = body.asr_model.strip() if body.asr_model is not None else None
    normalized_voice = body.voice.strip() if body.voice is not None else None
    if normalized_voice is not None and not normalized_voice:
        raise HTTPException(status_code=422, detail="voice must not be empty")
    ranges = _voice_ranges(body)
    job_request: JobRequest
    match body.source_type:
        case "epub":
            if normalized_asr_model is not None:
                msg = "asr_model is only valid for source_type='audio'"
                raise HTTPException(status_code=422, detail=msg)
            source_identity = _submission_source_identity(orch, body.source_path)
            if normalized_voice is not None or ranges:
                normalized_voice, ranges = await _canonicalize_voices(
                    orch,
                    body.target_language,
                    normalized_voice,
                    ranges,
                )
            normalized_ranges = _validate_voice_selection(
                orch.settings.orchestrator.data_dir / source_identity,
                normalized_voice,
                ranges,
            )
            job_request = EpubRequest(
                source_path=source_identity,
                source_language=body.source_language,
                target_language=body.target_language,
                voice=normalized_voice,
                voice_map=normalized_ranges,
            )
        case "audio":
            if ranges:
                msg = "voice_map is only valid for source_type='epub'"
                raise HTTPException(status_code=422, detail=msg)
            if not normalized_asr_model:
                msg = "asr_model is required for source_type='audio'"
                raise HTTPException(status_code=422, detail=msg)
            source_identity = _submission_source_identity(orch, body.source_path)
            if normalized_voice is not None:
                normalized_voice, _ = await _canonicalize_voices(orch, body.target_language, normalized_voice, ())
            job_request = AudioRequest(
                source_path=source_identity,
                source_language=body.source_language,
                target_language=body.target_language,
                asr_model=normalized_asr_model,
                voice=normalized_voice,
            )
        case _:
            msg = "Invalid source_type"
            raise HTTPException(status_code=400, detail=msg)
    if body.input_id is not None:
        try:
            InputStore(orch.settings.orchestrator.data_dir, create=False).promote(
                body.input_id,
                job_request.source_path,
            )
        except InputPathError as exc:
            raise HTTPException(status_code=422, detail="input identity does not match source_path") from exc
        except OSError as exc:
            logger.warning("Input promotion failed for %s: %s", body.input_id, exc)
            raise HTTPException(status_code=422, detail="input storage failed") from exc
    return job_request, strategy


def _submission_source_identity(orch: Orchestrator, source_path: str) -> str:
    """Validate a source and return its canonical data-directory-relative identity."""
    resolved = _resolve_submission_source(orch, source_path)
    try:
        return InputStore(orch.settings.orchestrator.data_dir, create=False).normalize_source_path(str(resolved))
    except OSError as exc:
        logger.warning("Source path %r could not be normalized: %s", source_path, exc)
        raise HTTPException(status_code=422, detail="Invalid source_path: source file is unavailable") from exc


def _resolve_stored_source(orch: Orchestrator, source_path: str) -> str:
    """Revalidate a stored source and return its canonical identity."""
    candidate = Path(source_path)
    data_dir = orch.settings.orchestrator.data_dir
    if candidate.is_absolute():
        try:
            relative_path = candidate.resolve(strict=False).relative_to(data_dir.resolve())
        except OSError as exc:
            logger.warning("Stored source path %r could not be resolved under %s: %s", source_path, data_dir, exc)
            msg = "Invalid source_path: source file is unavailable"
            raise HTTPException(status_code=422, detail=msg) from exc
        except ValueError as exc:
            logger.warning("Stored source path %r is outside data directory %s", source_path, data_dir)
            msg = "Invalid stored source_path"
            raise HTTPException(status_code=422, detail=msg) from exc
    else:
        relative_path = candidate
    return _submission_source_identity(orch, str(relative_path))


async def _build_retry_request(  # noqa: C901, PLR0912, PLR0915
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
        raise HTTPException(status_code=400, detail="Invalid executor strategy") from exc

    label = body.label if body.label is not None else source.label
    request: JobRequest
    match source.request:
        case EpubRequest(
            source_path=source_path,
            source_language=source_language,
            target_language=target_language,
            voice=voice,
            voice_map=voice_map,
        ):
            if body.source_path is not None:
                path = _submission_source_identity(orch, body.source_path)
            else:
                path = _resolve_stored_source(orch, source_path)
            if body.asr_model is not None and body.asr_model.strip():
                msg = "asr_model is only valid for source_type='audio'"
                raise HTTPException(status_code=422, detail=msg)
            selected_voice = body.voice if body.voice is not None else voice
            if selected_voice is not None:
                selected_voice = selected_voice.strip()
                if not selected_voice:
                    raise HTTPException(status_code=422, detail="voice override must not be empty")
            if body.voice_map is not None:
                try:
                    selected_map = tuple(
                        VoiceRange(item.start_chapter, item.end_chapter, item.voice) for item in body.voice_map
                    )
                except (TypeError, ValueError) as exc:
                    raise HTTPException(status_code=422, detail="invalid voice selection") from exc
            else:
                selected_map = voice_map
            selected_target = body.target_language if body.target_language is not None else target_language
            if selected_voice is not None or selected_map:
                selected_voice, selected_map = await _canonicalize_voices(
                    orch,
                    selected_target,
                    selected_voice,
                    selected_map,
                )
                selected_map = _validate_voice_selection(
                    orch.settings.orchestrator.data_dir / path,
                    selected_voice,
                    selected_map,
                )
            request = EpubRequest(
                source_path=path,
                source_language=body.source_language if body.source_language is not None else source_language,
                target_language=selected_target,
                voice=selected_voice,
                voice_map=selected_map,
            )
        case AudioRequest(
            source_path=source_path,
            source_language=source_language,
            target_language=target_language,
            asr_model=asr_model,
            voice=voice,
        ):
            if body.source_path is not None:
                path = _submission_source_identity(orch, body.source_path)
            else:
                path = _resolve_stored_source(orch, source_path)
            if body.voice_map:
                msg = "voice_map is only valid for source_type='epub'"
                raise HTTPException(status_code=422, detail=msg)
            selected_asr_model = body.asr_model.strip() if body.asr_model is not None else asr_model
            if not selected_asr_model:
                msg = "asr_model is required for source_type='audio'"
                raise HTTPException(status_code=422, detail=msg)
            selected_voice = body.voice if body.voice is not None else voice
            if selected_voice is not None:
                selected_voice = selected_voice.strip()
                if not selected_voice:
                    raise HTTPException(status_code=422, detail="voice override must not be empty")
                selected_voice, _ = await _canonicalize_voices(
                    orch,
                    body.target_language if body.target_language is not None else target_language,
                    selected_voice,
                    (),
                )
            request = AudioRequest(
                source_path=path,
                source_language=body.source_language if body.source_language is not None else source_language,
                target_language=body.target_language if body.target_language is not None else target_language,
                asr_model=selected_asr_model,
                voice=selected_voice,
            )
    return request, strategy, label


@router.post("", status_code=201, response_model=JobResponse)
async def submit_job(
    body: SubmitJobRequest,
    orch: OrchestratorDep,
    _token: RegistrationTokenDep,
) -> JobResponse:
    """Submit a new job for processing."""
    tracked: TrackedJob | None = None
    try:
        job_request, strategy = await _build_job_request(orch, body)
        if body.input_id is None:
            if body.label is None:
                tracked = await orch.submit_job(job_request, strategy)
            else:
                tracked = await orch.submit_job(job_request, strategy, label=body.label)
        elif body.label is None:
            tracked = await orch.submit_job(job_request, strategy, input_id=body.input_id)
        else:
            tracked = await orch.submit_job(job_request, strategy, label=body.label, input_id=body.input_id)
    except AcheronError as exc:
        raise HTTPException(status_code=422, detail=_error_response(exc).model_dump()) from exc
    except InputPathError as exc:
        raise HTTPException(status_code=422, detail="input identity does not match source_path") from exc
    except OSError as exc:
        logger.warning("Input promotion failed during submission: %s", exc)
        raise HTTPException(status_code=422, detail="input storage failed") from exc
    except HTTPException:
        raise
    finally:
        if tracked is None:
            await _cleanup_temporary_inputs(orch, _temporary_input_ids(body))

    warnings: list[str] = []
    try:
        warnings = _booting_tts_warnings(await orch.list_workers(), now=time.time())
    except Exception:
        logger.exception("Failed to inspect workers for job submission warnings")
    assert tracked is not None  # noqa: S101
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
        exc = JobNotFoundError("Job not found")
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
    the validation a real submit would experience. A temporary input remains
    available after a successful preview so the caller can submit that same
    input; every failed preflight path rolls it back.
    """
    succeeded = False
    try:
        job_request, strategy = await _build_job_request(orch, body)
        plan = await orch.preview_job(job_request, strategy)
    except AcheronError as exc:
        raise HTTPException(status_code=422, detail=_error_response(exc).model_dump()) from exc
    else:
        response = PlanResponse.from_plan(plan)
        succeeded = True
        return response
    finally:
        if not succeeded:
            await _cleanup_temporary_inputs(orch, _temporary_input_ids(body))


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: str, orch: OrchestratorDep) -> JobResponse:
    """Get job status and result."""
    tracked = await orch.get_job(job_id)
    if tracked is None:
        raise HTTPException(status_code=404, detail=_error_response(JobNotFoundError("Job not found")).model_dump())
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
            detail=_error_response(JobNotFoundError("Job not found")).model_dump(),
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
            worker_id=public_optional_worker_id(tracked.progress.current_worker_id),
            progress=JobProgress(
                completed_steps=tracked.progress.completed_steps,
                total_steps=tracked.progress.total_steps,
                current_step_id=tracked.progress.current_step_id,
                current_worker_type=tracked.progress.current_worker_type,
                current_worker_id=public_optional_worker_id(tracked.progress.current_worker_id),
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
        f"{public_worker_id(worker.worker_id)} ({math.floor(max(0.0, now - (worker.booting_since or 0.0)))}s elapsed)"
        for worker in affected
    )
    return [f"BOOTING TTS workers: {elapsed}; cold start typically takes 30\u201390 seconds."]


def _to_step_error_response(error: DomainStepError) -> StepErrorResponse:
    return StepErrorResponse(
        step_id=error.step_id,
        worker_type=error.worker_type,
        worker_id=public_optional_worker_id(error.worker_id),
        message=sanitise_public_message(error.message, fallback="step failed"),
        timestamp=error.timestamp,
    )


def _tracked_to_response(tracked: TrackedJob, warnings: list[str] | None = None) -> JobResponse:
    result = tracked.result
    voice_map: tuple[VoiceRange, ...] = ()
    match tracked.request:
        case AudioRequest(
            source_language=source_language,
            target_language=target_language,
            asr_model=asr_model,
            voice=voice,
        ):
            source_type = "audio"
            voice_map = ()
        case EpubRequest(
            source_language=source_language,
            target_language=target_language,
            voice=voice,
            voice_map=voice_map,
        ):
            source_type = "epub"
            asr_model = None

    progress = tracked.progress
    voice_map_payload: list[dict[str, int | str]] = [
        {
            "start_chapter": item.start_chapter,
            "end_chapter": item.end_chapter,
            "voice": item.voice,
        }
        for item in voice_map
    ]
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
        voice=voice,
        voice_map=voice_map_payload,
        executor_strategy=tracked.strategy,
        created_at=tracked.created_at,
        last_persisted_at=tracked.last_persisted_at,
        archived_at=tracked.archived_at,
        progress=JobProgress(
            completed_steps=progress.completed_steps,
            total_steps=progress.total_steps,
            current_step_id=progress.current_step_id,
            current_worker_type=progress.current_worker_type,
            current_worker_id=public_optional_worker_id(progress.current_worker_id),
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
                    content_type=public_content_type(output.content_type),
                )
                for index, output in enumerate(result.outputs)
            ]
            if result
            else []
        ),
        errors=([_to_step_error_response(error) for error in result.errors] if result else []),
        warnings=warnings if warnings is not None else [],
    )
