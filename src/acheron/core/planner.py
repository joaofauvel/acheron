"""Plan compilation from job requests."""

import logging
import re
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

from acheron.core.epub import read_epub_chapters
from acheron.core.errors import ChunkingTooLongForWorkerError, InvalidLanguagePathError, VoiceSelectionError
from acheron.core.models import (
    AudioRequest,
    EpubRequest,
    ExecutorStrategy,
    JobRequest,
    JsonValue,
    Plan,
    PlanStep,
    StepStatus,
    VoiceRange,
    VoiceSelection,
    WorkerCapabilities,
    WorkerType,
)

type WorkerCapabilityRecord = tuple[str | None, WorkerCapabilities]
type CapabilityInput = tuple[WorkerCapabilities, ...] | tuple[WorkerCapabilityRecord, ...]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChunkingLimits:
    """Chunking-step bounds the orchestrator passes to ``compile_plan``.

    Carries the operator-tunable ``max_chunk_length`` and the chars-per-token
    estimator used to convert it to a token budget against each text-input
    worker's ``max_input_tokens``.
    """

    max_chunk_length: int
    chars_per_token: int


def compile_plan(  # noqa: PLR0913
    request: JobRequest,
    strategy: ExecutorStrategy,
    capabilities: CapabilityInput,
    plan_id: str | None = None,
    job_id: str | None = None,
    *,
    chunking: ChunkingLimits | None = None,
    source_root: Path | None = None,
) -> Plan:
    """Compile a job request into a validated Plan DAG.

    Validates that available workers support the requested language path,
    then (if ``chunking`` is supplied) that the chunking step's
    ``max_chunk_length`` fits every text-input worker's ``max_input_tokens``.
    Finally generates the step sequence based on input type.

    Raises:
        InvalidLanguagePathError: If no workers can handle the language path.
        ChunkingTooLongForWorkerError: If ``chunking`` is supplied and
            ``max_chunk_length`` exceeds a text-input worker's
            ``max_input_tokens``.
    """
    records = _capability_records(capabilities)
    capability_values = tuple(capability for _, capability in records)
    _validate_language_path(request, capability_values)
    selection = _request_voice_selection(request)
    tts_records = tuple(
        (worker_id, capability)
        for worker_id, capability in records
        if capability.worker_type is WorkerType.TTS
        and request.target_language in capability.supported_languages_in
        and request.target_language in capability.supported_languages_out
    )
    selected_worker_id = select_voice_worker_id(selection, tts_records)
    selected_caps = _selected_voice_capabilities(selection, selected_worker_id, tts_records)
    selection = _canonicalize_selection(selection, selected_caps)
    if chunking is not None:
        _validate_chunking_fits_workers(
            capability_values,
            chunking.max_chunk_length,
            chars_per_token=chunking.chars_per_token,
        )

    match request:
        case EpubRequest():
            chapter_ids = _discover_epub_chapter_ids(request.source_path, source_root=source_root)
            steps = _epub_steps(request, chapter_ids, selection=selection, selected_worker_id=selected_worker_id)
            source_type = "epub"
        case AudioRequest():
            steps = _audio_steps(request, selection=selection, selected_worker_id=selected_worker_id)
            source_type = "audio"

    return Plan(
        plan_id=plan_id or f"plan-{uuid.uuid4().hex[:8]}",
        job_id=job_id or f"job-{uuid.uuid4().hex[:8]}",
        source_type=source_type,
        source_language=request.source_language,
        target_language=request.target_language,
        executor_strategy=strategy,
        steps=tuple(steps),
    )


def _capability_records(capabilities: CapabilityInput) -> tuple[WorkerCapabilityRecord, ...]:
    """Normalize records while keeping legacy capability-only inputs anonymous."""
    records: list[WorkerCapabilityRecord] = []
    for item in capabilities:
        if isinstance(item, WorkerCapabilities):
            records.append((None, item))
        else:
            worker_id, capability = item
            records.append((worker_id or None, capability))
    return tuple(records)


def _request_voice_selection(request: JobRequest) -> VoiceSelection:
    match request:
        case EpubRequest(voice=voice, voice_map=voice_map):
            return VoiceSelection(default_voice=voice, ranges=voice_map)
        case AudioRequest(voice=voice):
            return VoiceSelection(default_voice=voice, ranges=())


def _validate_language_path(request: JobRequest, caps: tuple[WorkerCapabilities, ...]) -> None:
    """Verify workers exist for each required step type.

    Raises:
        InvalidLanguagePathError: If the language path is unsupported.
    """
    src = request.source_language
    dst = request.target_language

    match request:
        case AudioRequest():
            if not _has_worker(WorkerType.ASR, caps, src, src):
                msg = f"No ASR worker supports language: {src}"
                raise InvalidLanguagePathError(msg)

    if src != dst and not _has_worker(WorkerType.TRANSLATION, caps, src, dst):
        msg = f"No translation worker supports: {src} → {dst}"
        raise InvalidLanguagePathError(msg)

    if not _has_worker(WorkerType.TTS, caps, dst, dst):
        msg = f"No TTS worker supports language: {dst}"
        raise InvalidLanguagePathError(msg)


def advertised_voices(capabilities: WorkerCapabilities) -> frozenset[str]:
    """Return non-empty canonical voice spellings advertised by a worker."""
    value = capabilities.metadata.get("speakers")
    if not isinstance(value, list):
        return frozenset()
    return frozenset(item.strip() for item in value if isinstance(item, str) and item.strip())


_VOICE_CREDENTIAL_RE = re.compile(
    r"\b(?:password|passwd|secret|token|api[_-]?key|authorization)\s*[:=]",
    re.IGNORECASE,
)
_VOICE_URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_VOICE_PATH_RE = re.compile(r"[/\\]|\.\.")


def _safe_voice(value: str) -> str:
    """Return a bounded, non-sensitive label suitable for public errors."""
    normalized = "".join(" " if not char.isprintable() else char for char in value)
    normalized = " ".join(normalized.split())[:80]
    if _VOICE_URL_RE.search(normalized) or _VOICE_PATH_RE.search(normalized) or _VOICE_CREDENTIAL_RE.search(normalized):
        return "<redacted>"
    return normalized


def _voice_error(requested: set[str], available: set[str]) -> VoiceSelectionError:
    requested_text = ", ".join(sorted((_safe_voice(value) for value in requested), key=str.casefold))
    available_text = ", ".join(sorted((_safe_voice(value) for value in available), key=str.casefold)) or "none"
    return VoiceSelectionError(f"Unsupported voice selection: {requested_text}; available voices: {available_text}")


def canonicalize_voice(name: str, capabilities: WorkerCapabilities) -> str:
    """Return the registered canonical spelling for a requested voice."""
    voices = advertised_voices(capabilities)
    normalized = name.strip().casefold()
    for voice in sorted(voices, key=lambda value: (value.casefold(), value)):
        if voice.casefold() == normalized:
            return voice
    raise _voice_error({name}, set(voices))


def select_voice_worker_id(
    selection: VoiceSelection,
    workers: tuple[WorkerCapabilityRecord, ...],
) -> str | None:
    """Select one deterministic TTS worker capable of every requested voice."""
    candidates = tuple((worker_id, caps) for worker_id, caps in workers if caps.worker_type is WorkerType.TTS)
    requested = set((selection.default_voice,) if selection.default_voice is not None else ())
    requested.update(item.voice for item in selection.ranges)
    available = set().union(*(set(advertised_voices(caps)) for _, caps in candidates)) if candidates else set()
    if not requested:
        return min((worker_id for worker_id, _ in candidates if worker_id), key=str.casefold, default=None)

    matching: list[str] = []
    for worker_id, caps in candidates:
        canonical = {voice.casefold() for voice in advertised_voices(caps)}
        if worker_id is not None and all(voice.casefold() in canonical for voice in requested):
            matching.append(worker_id)
    if not matching:
        raise _voice_error(requested, available)
    # Mutate neither the request nor its value objects; canonical payloads are built by the caller.
    return min(matching, key=lambda worker_id: (worker_id.casefold(), worker_id))


def _selected_voice_capabilities(
    selection: VoiceSelection,
    selected_worker_id: str | None,
    workers: tuple[WorkerCapabilityRecord, ...],
) -> WorkerCapabilities:
    """Find the capabilities used to canonicalize the planner's selection."""
    requested = set((selection.default_voice,) if selection.default_voice is not None else ())
    requested.update(item.voice for item in selection.ranges)
    matching = [
        (worker_id, caps)
        for worker_id, caps in workers
        if caps.worker_type is WorkerType.TTS
        and (
            not requested
            or all(voice.casefold() in {item.casefold() for item in advertised_voices(caps)} for voice in requested)
        )
    ]
    for worker_id, caps in matching:
        if worker_id == selected_worker_id:
            return caps
    if selected_worker_id is None and not requested and matching:
        return matching[0][1]
    msg = "No TTS worker can honor the selected voice worker"
    raise VoiceSelectionError(msg)


def _canonicalize_selection(selection: VoiceSelection, capabilities: WorkerCapabilities) -> VoiceSelection:
    default = canonicalize_voice(selection.default_voice, capabilities) if selection.default_voice is not None else None
    ranges = tuple(
        VoiceRange(item.start_chapter, item.end_chapter, canonicalize_voice(item.voice, capabilities))
        for item in selection.ranges
    )
    return VoiceSelection(default_voice=default, ranges=ranges)


def _has_worker(
    worker_type: WorkerType,
    caps: tuple[WorkerCapabilities, ...],
    lang_in: str,
    lang_out: str,
) -> bool:
    """Check if any worker of the given type supports the language pair."""
    return any(
        c.worker_type == worker_type and lang_in in c.supported_languages_in and lang_out in c.supported_languages_out
        for c in caps
    )


def _validate_chunking_fits_workers(
    capabilities: tuple[WorkerCapabilities, ...],
    chunking_max_length: int,
    chars_per_token: int,
) -> None:
    """Verify the chunking step's max_chunk_length fits each text-input worker's limit.

    A text-input worker is one whose ``max_input_tokens`` is set on its capabilities
    (TRANSLATION, TTS in v1). If any such worker has a lower per-chunk token limit
    than the chunking step's max_chunk_length allows (estimated at ``chars_per_token``
    per token), raises ``ChunkingTooLongForWorkerError`` so the caller fails the job
    at plan compile time, before any GPU time is spent.

    The caller is ``compile_plan``, which receives the values through
    :class:`ChunkingLimits` from the orchestrator. ``chars_per_token`` is a
    conservative chars-to-tokens estimate (1 = CJK worst case; higher values
    exploit Latin-script character efficiency).

    Raises:
        ValueError: If ``chars_per_token <= 0``.
        ChunkingTooLongForWorkerError: If ``chunking_max_length`` exceeds a
            text-input worker's ``max_input_tokens``.
    """
    if chars_per_token <= 0:
        msg = f"chars_per_token must be > 0, got {chars_per_token}"
        raise ValueError(msg)
    text_input_types = (WorkerType.TRANSLATION, WorkerType.TTS)
    estimated_tokens = chunking_max_length // chars_per_token
    min_text_input_tokens: int | None = None
    for step_type in text_input_types:
        for c in capabilities:
            if c.worker_type != step_type or c.max_input_tokens is None:
                continue
            if min_text_input_tokens is None or c.max_input_tokens < min_text_input_tokens:
                min_text_input_tokens = c.max_input_tokens
            if estimated_tokens > c.max_input_tokens:
                msg = (
                    f"Chunking max_chunk_length={chunking_max_length} chars "
                    f"exceeds {step_type.value} worker max_input_tokens="
                    f"{c.max_input_tokens} (estimated {estimated_tokens} tokens "
                    f"at chars_per_token={chars_per_token})"
                )
                logger.warning("chunking input-budget check failed: %s", msg)
                raise ChunkingTooLongForWorkerError(msg)
    logger.debug(
        "chunking input-budget validated: max_chunk_length=%d, chars_per_token=%d, "
        "estimated_tokens=%d, min text-input max_input_tokens=%s",
        chunking_max_length,
        chars_per_token,
        estimated_tokens,
        min_text_input_tokens,
    )


def _discover_epub_chapter_ids(source_path: str, *, source_root: Path | None = None) -> tuple[str, ...]:
    """Discover stable chapter IDs when the source is a readable EPUB.

    Planner tests and preview requests may reference a path that is not mounted
    in the planner process. Those plans carry no chapter metadata; resume then
    reports that limitation instead of guessing chapter identities.
    """
    path = Path(source_path)
    if source_root is not None and not path.is_absolute():
        path = source_root / path
    if not path.is_file():
        return ()
    try:
        return tuple(chapter.chapter_id for chapter in read_epub_chapters(path))
    except (OSError, UnicodeError, ValueError, zipfile.BadZipFile) as exc:
        logger.warning("EPUB chapter metadata unavailable for %s: %s", path, exc)
        return ()


def _chapter_payload(payload: dict[str, JsonValue], chapter_ids: tuple[str, ...]) -> dict[str, JsonValue]:
    if not chapter_ids:
        return payload
    return {**payload, "chapter_ids": list(chapter_ids)}


def _tts_payload(
    target_language: str,
    selection: VoiceSelection,
    *,
    include_voice_map: bool,
) -> dict[str, JsonValue]:
    """Build a voice-map payload without an explicit null voice override."""
    payload: dict[str, JsonValue] = {"target_language": target_language}
    if selection.default_voice is not None or not selection.ranges:
        payload["voice"] = selection.default_voice
    if include_voice_map:
        payload["voice_map"] = [
            {
                "start_chapter": item.start_chapter,
                "end_chapter": item.end_chapter,
                "voice": item.voice,
            }
            for item in selection.ranges
        ]
    return payload


def _epub_steps(
    request: EpubRequest,
    chapter_ids: tuple[str, ...] = (),
    *,
    selection: VoiceSelection | None = None,
    selected_worker_id: str | None = None,
) -> list[PlanStep]:
    """Generate EPUB stages and attach discovered chapter identities."""
    effective_selection = selection or VoiceSelection(default_voice=request.voice, ranges=request.voice_map)
    needs_translation = request.source_language != request.target_language
    translate_dep = "chunk"

    steps: list[PlanStep] = [
        PlanStep(
            step_id="extract",
            type=WorkerType.EXTRACTION,
            depends_on=(),
            status=StepStatus.PENDING,
            payload=_chapter_payload({"source_path": request.source_path}, chapter_ids),
        ),
        PlanStep(
            step_id="chunk",
            type=WorkerType.CHUNKING,
            depends_on=("extract",),
            status=StepStatus.PENDING,
            payload=_chapter_payload({}, chapter_ids),
        ),
    ]

    if needs_translation:
        steps.append(
            PlanStep(
                step_id="translate",
                type=WorkerType.TRANSLATION,
                depends_on=("chunk",),
                status=StepStatus.PENDING,
                payload=_chapter_payload(
                    {"source_language": request.source_language, "target_language": request.target_language},
                    chapter_ids,
                ),
            ),
        )
        translate_dep = "translate"

    steps.extend(
        [
            PlanStep(
                step_id="synthesize",
                type=WorkerType.TTS,
                depends_on=(translate_dep,),
                status=StepStatus.PENDING,
                payload=_chapter_payload(
                    _tts_payload(request.target_language, effective_selection, include_voice_map=True),
                    chapter_ids,
                ),
                selected_worker_id=selected_worker_id,
            ),
            PlanStep(
                step_id="package",
                type=WorkerType.PACKAGING,
                depends_on=("synthesize",),
                status=StepStatus.PENDING,
                payload=_chapter_payload({}, chapter_ids),
            ),
        ]
    )

    return steps


def _audio_steps(
    request: AudioRequest,
    *,
    selection: VoiceSelection | None = None,
    selected_worker_id: str | None = None,
) -> list[PlanStep]:
    """Generate step sequence for audio input."""
    effective_selection = selection or VoiceSelection(default_voice=request.voice, ranges=())
    needs_translation = request.source_language != request.target_language
    translate_dep = "chunk"

    steps: list[PlanStep] = [
        PlanStep(
            step_id="extract",
            type=WorkerType.EXTRACTION,
            depends_on=(),
            status=StepStatus.PENDING,
            payload={"source_path": request.source_path},
        ),
        PlanStep(
            step_id="transcribe",
            type=WorkerType.ASR,
            depends_on=("extract",),
            status=StepStatus.PENDING,
            payload={"source_language": request.source_language, "asr_model": request.asr_model},
        ),
        PlanStep(
            step_id="chunk",
            type=WorkerType.CHUNKING,
            depends_on=("transcribe",),
            status=StepStatus.PENDING,
            payload={},
        ),
    ]

    if needs_translation:
        steps.append(
            PlanStep(
                step_id="translate",
                type=WorkerType.TRANSLATION,
                depends_on=("chunk",),
                status=StepStatus.PENDING,
                payload={"source_language": request.source_language, "target_language": request.target_language},
            ),
        )
        translate_dep = "translate"

    steps.extend(
        [
            PlanStep(
                step_id="synthesize",
                type=WorkerType.TTS,
                depends_on=(translate_dep,),
                status=StepStatus.PENDING,
                payload=_tts_payload(request.target_language, effective_selection, include_voice_map=False),
                selected_worker_id=selected_worker_id,
            ),
            PlanStep(
                step_id="package",
                type=WorkerType.PACKAGING,
                depends_on=("synthesize",),
                status=StepStatus.PENDING,
                payload={},
            ),
        ]
    )

    return steps
