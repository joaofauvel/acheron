"""Plan compilation from job requests."""

import logging
import uuid
from dataclasses import dataclass

from acheron.core.errors import ChunkingTooLongForWorkerError, InvalidLanguagePathError
from acheron.core.models import (
    AudioRequest,
    EpubRequest,
    ExecutorStrategy,
    JobRequest,
    Plan,
    PlanStep,
    StepStatus,
    WorkerCapabilities,
    WorkerType,
)

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


def compile_plan(
    request: JobRequest,
    strategy: ExecutorStrategy,
    capabilities: tuple[WorkerCapabilities, ...],
    plan_id: str | None = None,
    job_id: str | None = None,
    *,
    chunking: ChunkingLimits | None = None,
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
    _validate_language_path(request, capabilities)
    if chunking is not None:
        _validate_chunking_fits_workers(
            capabilities,
            chunking.max_chunk_length,
            chars_per_token=chunking.chars_per_token,
        )

    match request:
        case EpubRequest():
            steps = _epub_steps(request)
            source_type = "epub"
        case AudioRequest():
            steps = _audio_steps(request)
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


def _epub_steps(request: EpubRequest) -> list[PlanStep]:
    """Generate step sequence for EPUB input."""
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
            step_id="chunk",
            type=WorkerType.CHUNKING,
            depends_on=("extract",),
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
                payload={"target_language": request.target_language},
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


def _audio_steps(request: AudioRequest) -> list[PlanStep]:
    """Generate step sequence for audio input."""
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
                payload={"target_language": request.target_language},
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
