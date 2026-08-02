"""Public response mappings for job routes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from acheron.core.errors import AcheronError, sanitise_public_message, sanitise_public_remediation
from acheron.core.models import AudioRequest, EpubRequest, StepError as DomainStepError, VoiceRange
from acheron.core.schemas import (
    ErrorResponse,
    JobProgress,
    JobResponse,
    OutputSummary,
    StepError as StepErrorResponse,
)
from acheron.shell.api.public import (
    public_content_type,
    public_filename,
    public_label,
    public_language,
    public_model,
    public_optional_worker_id,
)

if TYPE_CHECKING:
    from acheron.shell.job_store import TrackedJob


def error_response(exc: AcheronError) -> ErrorResponse:
    """Map a domain error to its public response shape."""
    return ErrorResponse(
        type=type(exc).__name__,
        message=sanitise_public_message(str(exc)),
        remediation=(sanitise_public_remediation(exc.remediation) if exc.remediation is not None else None),
    )


def to_step_error_response(error: DomainStepError) -> StepErrorResponse:
    """Map a domain step error to its public response shape."""
    return StepErrorResponse(
        step_id=error.step_id,
        worker_type=error.worker_type,
        worker_id=public_optional_worker_id(error.worker_id),
        message=sanitise_public_message(error.message, fallback="step failed"),
        timestamp=error.timestamp,
    )


def tracked_to_response(tracked: TrackedJob, warnings: list[str] | None = None) -> JobResponse:
    """Map tracked job state to its public response shape."""
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
            "voice": public_label(item.voice) or "<redacted>",
        }
        for item in voice_map
    ]
    return JobResponse(
        job_id=tracked.job_id,
        status=tracked.status,
        plan_id=tracked.plan.plan_id if tracked.plan else None,
        label=public_label(tracked.label),
        retries_from=tracked.retries_from,
        source_type=source_type,
        source_language=public_language(source_language),
        target_language=public_language(target_language),
        asr_model=public_model(asr_model),
        voice=public_label(voice),
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
                    filename=public_filename(output.filename),
                    size_bytes=output.size_bytes,
                    content_type=public_content_type(output.content_type),
                    metadata=dict(output.metadata),
                )
                for index, output in enumerate(result.outputs[:1000])
            ]
            if result
            else []
        ),
        errors=([to_step_error_response(error) for error in result.errors[:1000]] if result else []),
        warnings=(warnings or [])[:100],
    )
