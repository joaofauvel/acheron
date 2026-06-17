"""Plan compilation from job requests."""

import uuid

from acheron.core.errors import InvalidLanguagePathError
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


def compile_plan(
    request: JobRequest,
    strategy: ExecutorStrategy,
    capabilities: tuple[WorkerCapabilities, ...],
    plan_id: str | None = None,
    job_id: str | None = None,
) -> Plan:
    """Compile a job request into a validated Plan DAG.

    Validates that available workers support the requested language path,
    then generates the appropriate step sequence based on input type.

    Raises:
        InvalidLanguagePathError: If no workers can handle the language path.
    """
    _validate_language_path(request, capabilities)

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

    if not _has_worker(WorkerType.TRANSLATION, caps, src, dst):
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


def _epub_steps(request: EpubRequest) -> list[PlanStep]:
    """Generate step sequence for EPUB input."""
    return [
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
        PlanStep(
            step_id="translate",
            type=WorkerType.TRANSLATION,
            depends_on=("chunk",),
            status=StepStatus.PENDING,
            payload={"source_language": request.source_language, "target_language": request.target_language},
        ),
        PlanStep(
            step_id="synthesize",
            type=WorkerType.TTS,
            depends_on=("translate",),
            status=StepStatus.PENDING,
            payload={"target_language": request.target_language},
            batch=True,
        ),
        PlanStep(
            step_id="package",
            type=WorkerType.PACKAGING,
            depends_on=("synthesize",),
            status=StepStatus.PENDING,
            payload={},
        ),
    ]


def _audio_steps(request: AudioRequest) -> list[PlanStep]:
    """Generate step sequence for audio input."""
    return [
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
        PlanStep(
            step_id="translate",
            type=WorkerType.TRANSLATION,
            depends_on=("chunk",),
            status=StepStatus.PENDING,
            payload={"source_language": request.source_language, "target_language": request.target_language},
        ),
        PlanStep(
            step_id="synthesize",
            type=WorkerType.TTS,
            depends_on=("translate",),
            status=StepStatus.PENDING,
            payload={"target_language": request.target_language},
            batch=True,
        ),
        PlanStep(
            step_id="package",
            type=WorkerType.PACKAGING,
            depends_on=("synthesize",),
            status=StepStatus.PENDING,
            payload={},
        ),
    ]
