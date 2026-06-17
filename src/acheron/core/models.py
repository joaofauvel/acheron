from dataclasses import dataclass, field
from enum import Enum

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]


class WorkerType(Enum):
    EXTRACTION = "extraction"
    CHUNKING = "chunking"
    TRANSLATION = "translation"
    ASR = "asr"
    TTS = "tts"
    PACKAGING = "packaging"


class JobStatus(Enum):
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"


class StepStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass(frozen=True)
class WorkerCapabilities:
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
    job_id: str
    job_type: WorkerType
    payload: dict[str, JsonValue]
    chapter_id: str
    sequence_ids: tuple[int, ...] | None = None


@dataclass(frozen=True)
class OutputFile:
    path: str
    filename: str
    size_bytes: int
    checksum: str
    content_type: str


@dataclass(frozen=True)
class JobMetrics:
    duration_seconds: float
    gpu_seconds: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_estimate: float | None = None


@dataclass(frozen=True)
class JobResult:
    job_id: str
    status: JobStatus
    outputs: tuple[OutputFile, ...]
    metrics: JobMetrics
    error: str | None = None


@dataclass(frozen=True)
class PlanStep:
    step_id: str
    type: WorkerType
    depends_on: tuple[str, ...]
    status: StepStatus
    payload: dict[str, JsonValue]
    batch: bool = False


@dataclass(frozen=True)
class Plan:
    plan_id: str
    job_id: str
    source_type: str
    source_language: str
    target_language: str
    executor_strategy: str
    steps: tuple[PlanStep, ...]


@dataclass(frozen=True)
class PlanResult:
    plan_id: str
    status: str
    completed_steps: int
    total_steps: int
    outputs: tuple[OutputFile, ...]
    total_cost: float
    total_duration_seconds: float


@dataclass(frozen=True)
class BatchJob:
    batch_id: str
    jobs: tuple[Job, ...]


@dataclass(frozen=True)
class BatchStatus:
    batch_id: str
    total: int
    completed: int
    failed: int
    pending: int
    results: tuple[JobResult, ...]
