"""Core data models and enums for the Acheron pipeline."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum

from pydantic import TypeAdapter

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


class CostBasis(Enum):
    """Confidence level for a per-job cost estimate (since Layer 8a)."""

    MEASURED = "measured"
    CACHED = "cached"
    STATIC = "static"
    STUB = "stub"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CostEstimate:
    """Execution-time cost estimate with its pricing provenance."""

    cost: float | None
    basis: CostBasis
    rate_per_hour: float | None = None
    gpu_type: str | None = None
    secure_cloud: bool | None = None
    queried_at: datetime | None = None
    cache_age_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.queried_at is not None:
            if self.queried_at.tzinfo is None or self.queried_at.utcoffset() is None:
                msg = "queried_at must be timezone-aware"
                raise ValueError(msg)
            object.__setattr__(self, "queried_at", self.queried_at.astimezone(UTC))
        if self.cache_age_seconds is not None and self.cache_age_seconds < 0:
            msg = "cache_age_seconds must be non-negative"
            raise ValueError(msg)


@dataclass(frozen=True)
class CostBreakdown:
    """Cost evidence for one executed plan step."""

    step_id: str
    worker_type: WorkerType
    worker_id: str | None
    gpu_seconds: float | None
    estimate: CostEstimate


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
    max_input_tokens: int | None = None  # per-chunk input token limit; None = unbounded
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
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class JobMetrics:
    """Timing and cost data for a completed job."""

    duration_seconds: float
    gpu_seconds: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_estimate: CostEstimate | None = None

    def model_dump_json(self) -> bytes:
        """Pydantic-style JSON serialisation shim for the multipart metrics part.

        ``JobMetrics`` is a frozen dataclass (matches the other ``@dataclass``
        value objects in this module) but the SDK / orchestrator transport code
        treats it like a pydantic model on the wire.  ``TypeAdapter.dump_json``
        gives us a single source of truth without converting the in-memory type.
        """
        return TypeAdapter(JobMetrics).dump_json(self)


@dataclass(frozen=True)
class StepError:
    """Sanitized failure attribution for one execution step."""

    step_id: str | None
    worker_type: WorkerType | None
    worker_id: str | None
    message: str
    timestamp: datetime


@dataclass(frozen=True)
class JobResult:
    """Outcome of executing a job."""

    job_id: str
    status: JobStatus
    outputs: tuple[OutputFile, ...]
    metrics: JobMetrics
    error: str | None = None
    worker_id: str | None = None

    def model_dump_json(self) -> bytes:
        """Pydantic-style JSON serialisation for the error-response body.

        ``JobResult`` is a frozen dataclass; the SDK / orchestrator wire
        contract uses pydantic's ``TypeAdapter`` to round-trip the JSON.
        Returns ``bytes`` to match pydantic v2's signature.
        """
        return TypeAdapter(JobResult).dump_json(self)


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
    errors: tuple[StepError, ...] = ()
    total_cost_basis: CostBasis | None = None
    cost_breakdown: tuple[CostBreakdown, ...] = ()


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
