"""Core data models and enums for the Acheron pipeline."""

from dataclasses import dataclass, field
from enum import Enum

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]


class WorkerType(Enum):
    """Type of compute worker in the pipeline."""

    EXTRACTION = "extraction"
    CHUNKING = "chunking"
    TRANSLATION = "translation"
    ASR = "asr"
    TTS = "tts"
    PACKAGING = "packaging"


SUPPORTED_LANGUAGES = frozenset({"en", "es", "fr", "de"})


class JobStatus(Enum):
    """Outcome status of a completed job."""

    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"


class StepStatus(Enum):
    """Lifecycle status of a pipeline step."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


class PlanStatus(Enum):
    """Lifecycle status of a plan (TrackedJob.status) and its final outcome (PlanResult.status)."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"


class ExecutorStrategy(Enum):
    """Plan execution strategy."""

    SEQUENTIAL = "sequential"
    ASYNC = "async"
    STREAMING = "streaming"


class WorkerStatus(Enum):
    """Health status of a registered worker."""

    HEALTHY = "healthy"
    BOOTING = "booting"
    OFFLINE = "offline"


@dataclass(frozen=True)
class WorkerCapabilities:
    """Describes a worker's supported types, languages, and formats."""

    worker_type: WorkerType
    supported_languages_in: frozenset[str]
    supported_languages_out: frozenset[str]
    supported_formats_in: frozenset[str]
    supported_formats_out: frozenset[str]
    max_payload_bytes: int | None
    batch_capable: bool
    model_source: str | None
    metadata: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True)
class Job:
    """A unit of work dispatched to a worker."""

    job_id: str
    job_type: WorkerType
    payload: dict[str, JsonValue]
    chapter_id: str
    sequence_ids: tuple[int, ...] | None = None


@dataclass(frozen=True)
class OutputFile:
    """An artifact produced by a pipeline step."""

    path: str
    filename: str
    size_bytes: int
    checksum: str
    content_type: str


@dataclass(frozen=True)
class JobMetrics:
    """Timing and cost data for a completed job."""

    duration_seconds: float
    gpu_seconds: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_estimate: float | None = None


@dataclass(frozen=True)
class JobResult:
    """Outcome of executing a job."""

    job_id: str
    status: JobStatus
    outputs: tuple[OutputFile, ...]
    metrics: JobMetrics
    error: str | None = None


@dataclass(frozen=True)
class PlanStep:
    """A single step in a pipeline plan DAG."""

    step_id: str
    type: WorkerType
    depends_on: tuple[str, ...]
    status: StepStatus
    payload: dict[str, JsonValue]


@dataclass(frozen=True)
class Plan:
    """An immutable DAG of pipeline steps for a job."""

    plan_id: str
    job_id: str
    source_type: str
    source_language: str
    target_language: str
    executor_strategy: ExecutorStrategy
    steps: tuple[PlanStep, ...]


@dataclass(frozen=True)
class PlanResult:
    """Outcome of executing a full plan."""

    plan_id: str
    status: PlanStatus
    completed_steps: int
    total_steps: int
    outputs: tuple[OutputFile, ...]
    total_cost: float
    total_duration_seconds: float
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class EpubRequest:
    """Job request for EPUB input."""

    source_path: str
    source_language: str
    target_language: str


@dataclass(frozen=True)
class AudioRequest:
    """Job request for audio input."""

    source_path: str
    source_language: str
    target_language: str
    asr_model: str | None = None


type JobRequest = EpubRequest | AudioRequest


@dataclass(frozen=True)
class Chunk:
    """A text segment produced by the chunking engine."""

    chapter_id: str
    sequence_id: int
    text: str
