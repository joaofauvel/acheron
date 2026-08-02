"""Job request validation, construction, and submission routes."""

from __future__ import annotations

import logging
import math
import re
import time
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import HTTPException

from acheron.core.epub import read_epub_chapters
from acheron.core.errors import (
    AcheronError,
    JobNotFoundError,
    sanitise_public_message,
)
from acheron.core.models import (
    AudioRequest,
    EpubRequest,
    ExecutorStrategy,
    JobRequest,
    VoiceRange,
    VoiceSelection,
    WorkerStatus,
    WorkerType,
)
from acheron.core.schemas import JobResponse, PlanResponse
from acheron.shell.api.deps import OrchestratorDep, RegistrationTokenDep  # noqa: TC001
from acheron.shell.api.public import public_worker_id
from acheron.shell.api.routes.job_responses import error_response, tracked_to_response
from acheron.shell.api.schemas import RetryJobRequest, SubmitJobRequest  # noqa: TC001
from acheron.shell.input_store import InputPathError, InputStore
from acheron.shell.stores.base import StoreError

if TYPE_CHECKING:
    from acheron.shell.job_store import TrackedJob
    from acheron.shell.orchestrator import Orchestrator
    from acheron.shell.registry import RegisteredWorker

logger = logging.getLogger(__name__)

_INPUT_ID_RE = re.compile(r"[0-9a-f]{32}")
_INPUT_SOURCE_RE = re.compile(r"^inputs/([0-9a-f]{32})/.+$")


async def cleanup_temporary_inputs(orch: Orchestrator, input_ids: set[str]) -> None:
    """Best-effort cleanup for failed temporary-input submissions."""
    for input_id in sorted(input_ids):
        try:
            await orch.delete_input(input_id)
        except (InputPathError, OSError) as exc:
            logger.warning("Temporary input cleanup failed for %s: %s", input_id, exc)
        except Exception:
            logger.exception("Temporary input cleanup failed for %s", input_id)


def temporary_input_ids(body: SubmitJobRequest) -> set[str]:
    """Extract only canonical temporary-input identities from a request."""
    identities: set[str] = set()
    if body.input_id is not None and _INPUT_ID_RE.fullmatch(body.input_id):
        identities.add(body.input_id)
    source_match = _INPUT_SOURCE_RE.fullmatch(body.source_path)
    if source_match is not None:
        identities.add(source_match.group(1))
    return identities


def resolve_submission_source(orch: Orchestrator, source_path: str) -> Path:
    """Resolve a user-supplied relative source path to an allowlisted regular file."""
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


def voice_ranges(body: SubmitJobRequest) -> tuple[VoiceRange, ...]:
    """Convert strict wire ranges into canonical domain values."""
    try:
        return tuple(VoiceRange(item.start_chapter, item.end_chapter, item.voice) for item in body.voice_map)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=sanitise_public_message(str(exc))) from exc


async def canonicalize_voices(
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


def validate_voice_selection(
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


async def build_job_request(  # noqa: C901, PLR0912
    orch: Orchestrator,
    body: SubmitJobRequest,
) -> tuple[JobRequest, ExecutorStrategy]:
    """Validate a submission body and resolve its source path."""
    try:
        strategy = ExecutorStrategy(body.executor_strategy)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid executor strategy") from exc

    normalized_asr_model = body.asr_model.strip() if body.asr_model is not None else None
    normalized_voice = body.voice.strip() if body.voice is not None else None
    if normalized_voice is not None and not normalized_voice:
        raise HTTPException(status_code=422, detail="voice must not be empty")
    ranges = voice_ranges(body)
    job_request: JobRequest
    match body.source_type:
        case "epub":
            if normalized_asr_model is not None:
                msg = "asr_model is only valid for source_type='audio'"
                raise HTTPException(status_code=422, detail=msg)
            source_identity = submission_source_identity(orch, body.source_path)
            if normalized_voice is not None or ranges:
                normalized_voice, ranges = await canonicalize_voices(
                    orch,
                    body.target_language,
                    normalized_voice,
                    ranges,
                )
            normalized_ranges = validate_voice_selection(
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
            source_identity = submission_source_identity(orch, body.source_path)
            if normalized_voice is not None:
                normalized_voice, _ = await canonicalize_voices(orch, body.target_language, normalized_voice, ())
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


def submission_source_identity(orch: Orchestrator, source_path: str) -> str:
    """Validate a source and return its canonical data-directory-relative identity."""
    resolved = resolve_submission_source(orch, source_path)
    try:
        return InputStore(orch.settings.orchestrator.data_dir, create=False).normalize_source_path(str(resolved))
    except OSError as exc:
        logger.warning("Source path %r could not be normalized: %s", source_path, exc)
        raise HTTPException(status_code=422, detail="Invalid source_path: source file is unavailable") from exc


def resolve_stored_source(orch: Orchestrator, source_path: str) -> str:
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
    return submission_source_identity(orch, str(relative_path))


async def build_retry_request(  # noqa: C901, PLR0912, PLR0915
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
                path = submission_source_identity(orch, body.source_path)
            else:
                path = resolve_stored_source(orch, source_path)
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
                selected_voice, selected_map = await canonicalize_voices(
                    orch,
                    selected_target,
                    selected_voice,
                    selected_map,
                )
                selected_map = validate_voice_selection(
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
                path = submission_source_identity(orch, body.source_path)
            else:
                path = resolve_stored_source(orch, source_path)
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
                selected_voice, _ = await canonicalize_voices(
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


def booting_tts_warnings(
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


async def submit_job(
    body: SubmitJobRequest,
    orch: OrchestratorDep,
    _token: RegistrationTokenDep,
) -> JobResponse:
    """Submit a new job for processing."""
    tracked: TrackedJob | None = None
    try:
        job_request, strategy = await build_job_request(orch, body)
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
        raise HTTPException(status_code=422, detail=error_response(exc).model_dump()) from exc
    except InputPathError as exc:
        raise HTTPException(status_code=422, detail="input identity does not match source_path") from exc
    except OSError as exc:
        logger.warning("Input promotion failed during submission: %s", exc)
        raise HTTPException(status_code=422, detail="input storage failed") from exc
    except HTTPException:
        raise
    finally:
        if tracked is None:
            await cleanup_temporary_inputs(orch, temporary_input_ids(body))

    try:
        warnings = booting_tts_warnings(await orch.list_workers(), now=time.time())
    except StoreError:
        logger.exception("Failed to inspect workers for job submission warnings")
        warnings = []
    assert tracked is not None  # noqa: S101
    return tracked_to_response(tracked, warnings=warnings)


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
        raise HTTPException(status_code=404, detail=error_response(exc).model_dump()) from exc
    request, strategy, label = await build_retry_request(orch, source, body)
    try:
        tracked = await orch.submit_retry(job_id, request, strategy, label=label)
    except AcheronError as exc:
        raise HTTPException(status_code=422, detail=error_response(exc).model_dump()) from exc
    return tracked_to_response(tracked)


async def preview_job(
    body: SubmitJobRequest,
    orch: OrchestratorDep,
    _token: RegistrationTokenDep,
) -> PlanResponse:
    """Compile a plan without persisting a job or scheduling execution."""
    succeeded = False
    try:
        job_request, strategy = await build_job_request(orch, body)
        plan = await orch.preview_job(job_request, strategy)
    except AcheronError as exc:
        raise HTTPException(status_code=422, detail=error_response(exc).model_dump()) from exc
    else:
        response = PlanResponse.from_plan(plan)
        succeeded = True
        return response
    finally:
        if not succeeded:
            await cleanup_temporary_inputs(orch, temporary_input_ids(body))
