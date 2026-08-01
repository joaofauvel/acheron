"""Core data models and enums for the Acheron pipeline."""

import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum

from pydantic import TypeAdapter

from acheron.core.errors import sanitise_public_message

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
_MAX_WORKER_ERROR_LENGTH = 512
_WORKER_URL_RE = re.compile(r"(?:https?|grpc|grpcs|redis|rediss)://[^\s,;]+", re.IGNORECASE)
_WORKER_HOST_PORT_RE = re.compile(
    r"(?<![\w.-])(?:localhost|127(?:\.\d{1,3}){3}|[a-z0-9.-]+)(?::\d{2,5})(?!\w)", re.IGNORECASE
)
_WORKER_SECRET_RE = re.compile(
    r"(?:authorization|bearer|token|password|secret|api[_ -]?key|credential)"
    r"\s*(?:[:=]|is)\s*(?:bearer\s+)?(?:\[[^\]]+\]|[^\s,;]+)",
    re.IGNORECASE,
)
_WORKER_BARE_SECRET_RE = re.compile(
    r"\b(?:token|password|secret|api[_ -]?key|authorization|credential)\b\s+(?:bearer\s+)?"
    r"(?!header\b|missing\b|invalid\b|provided\b|required\b|is\b|was\b|not\b)[^\s,;]+",
    re.IGNORECASE,
)
_WORKER_JSON_SECRET_RE = re.compile(
    r"[\"']?(?:authorization|bearer|token|password|secret|api[_ -]?key|credential)"
    r"[\"']?\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|\[[^\]]+\]|[^,\s}]+)",
    re.IGNORECASE,
)
_WORKER_BEARER_RE = re.compile(r"\bbearer\s+[^\s,;]+", re.IGNORECASE)
_WORKER_TRACE_START_RE = re.compile(r"\btraceback\b|^\s*(?:file\s+|during handling|the above exception)", re.IGNORECASE)
_WORKER_DIAGNOSTIC_RE = re.compile(
    r"\b(?:request[_ -]?id|request\s+(?:body|details|payload)|"
    r"provider\s+(?:request|details|body|response)|response\s*(?:body|details|data)?|"
    r"payload|json)\b",
    re.IGNORECASE,
)
_WORKER_PROVIDER_RE = re.compile(r"\bprovider\b.*\b(?:error|request|details|body|response)\b", re.IGNORECASE)


def sanitize_worker_error(message: str) -> str:
    """Return a bounded operational summary without sensitive diagnostics."""
    lines: list[str] = []
    for raw_line in str(message).splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if _WORKER_TRACE_START_RE.search(stripped):
            break
        provider_diagnostic = _WORKER_PROVIDER_RE.search(stripped)
        if provider_diagnostic:
            prefix = stripped[: provider_diagnostic.start()].rstrip(" :;,-")
            if prefix:
                lines.append(f"{prefix}; provider check failed")
            else:
                lines.append("provider check failed")
            break
        diagnostic = _WORKER_DIAGNOSTIC_RE.search(stripped)
        if diagnostic:
            prefix = stripped[: diagnostic.start()].rstrip(" :;,-")
            if _WORKER_PROVIDER_RE.search(stripped):
                prefix = re.split(r"\bprovider\b", prefix, maxsplit=1, flags=re.IGNORECASE)[0].rstrip(" :;,-")
                prefix = f"{prefix}; " if prefix else ""
                lines.append(f"{prefix}provider check failed")
            elif prefix:
                lines.append(prefix)
            break
        lines.append(stripped)
    sanitized = " ".join(lines) or "health check failed"
    sanitized = _WORKER_URL_RE.sub("[redacted endpoint]", sanitized)
    sanitized = _WORKER_HOST_PORT_RE.sub("[redacted endpoint]", sanitized)
    sanitized = _WORKER_BEARER_RE.sub("[redacted credential]", sanitized)
    sanitized = _WORKER_JSON_SECRET_RE.sub("[redacted credential]", sanitized)
    sanitized = _WORKER_SECRET_RE.sub("[redacted credential]", sanitized)
    sanitized = _WORKER_BARE_SECRET_RE.sub("[redacted credential]", sanitized)
    return sanitise_public_message(sanitized, fallback="health check failed")[:_MAX_WORKER_ERROR_LENGTH]


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
        for name, value in (("cost", self.cost), ("rate_per_hour", self.rate_per_hour)):
            if value is not None and not math.isfinite(value):
                msg = f"{name} must be finite"
                raise ValueError(msg)
        if self.queried_at is not None:
            if self.queried_at.tzinfo is None or self.queried_at.utcoffset() is None:
                msg = "queried_at must be timezone-aware"
                raise ValueError(msg)
            object.__setattr__(self, "queried_at", self.queried_at.astimezone(UTC))
        if self.cache_age_seconds is not None and (
            not math.isfinite(self.cache_age_seconds) or self.cache_age_seconds < 0
        ):
            msg = "cache_age_seconds must be finite and non-negative"
            raise ValueError(msg)


@dataclass(frozen=True)
class CostBreakdown:
    """Cost evidence for one executed plan step."""

    step_id: str
    worker_type: WorkerType
    worker_id: str | None
    gpu_seconds: float | None
    estimate: CostEstimate

    def __post_init__(self) -> None:
        if self.gpu_seconds is not None and not math.isfinite(self.gpu_seconds):
            msg = "gpu_seconds must be finite"
            raise ValueError(msg)


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
class WorkerErrorEvent:
    """Sanitized health failure retained for worker recovery history."""

    timestamp: datetime
    message: str
    consecutive_failures: int

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            msg = "timestamp must be timezone-aware"
            raise ValueError(msg)
        object.__setattr__(self, "timestamp", self.timestamp.astimezone(UTC))
        if self.consecutive_failures < 1:
            msg = "consecutive_failures must be positive"
            raise ValueError(msg)


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
    selected_worker_id: str | None = None


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


def _canonical_voice(value: str, field_name: str = "voice") -> str:
    """Normalize a user-facing voice name without changing its canonical spelling."""
    normalized = value.strip()
    if not normalized:
        msg = f"{field_name} must not be empty"
        raise ValueError(msg)
    if any(char in normalized for char in "\r\n"):
        msg = f"{field_name} must not contain newlines"
        raise ValueError(msg)
    return normalized


@dataclass(frozen=True)
class VoiceRange:
    """Inclusive chapter range selecting one canonical voice."""

    start_chapter: int
    end_chapter: int
    voice: str

    def __post_init__(self) -> None:
        if isinstance(self.start_chapter, bool) or isinstance(self.end_chapter, bool):
            msg = "chapter numbers must be integers"
            raise TypeError(msg)
        object.__setattr__(self, "voice", _canonical_voice(self.voice))


@dataclass(frozen=True)
class VoiceSelection:
    """Validated default and per-chapter voice selection."""

    default_voice: str | None
    ranges: tuple[VoiceRange, ...]

    def __post_init__(self) -> None:
        if self.default_voice is not None:
            object.__setattr__(self, "default_voice", _canonical_voice(self.default_voice, "default_voice"))
        object.__setattr__(self, "ranges", tuple(self.ranges))

    @classmethod
    def from_ranges(
        cls,
        default_voice: str | None,
        ranges: tuple[VoiceRange, ...],
        chapter_count: int,
    ) -> VoiceSelection:
        """Build a selection after validating ranges against discovered chapters."""
        if isinstance(chapter_count, bool) or chapter_count < 1:
            msg = "chapter_count must be positive"
            raise ValueError(msg)
        normalized_default = _canonical_voice(default_voice, "default_voice") if default_voice is not None else None
        ordered = tuple(sorted(ranges, key=lambda item: (item.start_chapter, item.end_chapter)))
        previous: VoiceRange | None = None
        covered: set[int] = set()
        for item in ordered:
            if item.start_chapter < 1 or item.end_chapter < 1:
                msg = "chapter numbers must be positive"
                raise ValueError(msg)
            if item.start_chapter > item.end_chapter:
                msg = "chapter range must not be reversed"
                raise ValueError(msg)
            if item.end_chapter > chapter_count:
                msg = "chapter range is beyond the discovered chapter count"
                raise ValueError(msg)
            if previous is not None and item.start_chapter <= previous.end_chapter:
                msg = "chapter ranges overlap"
                raise ValueError(msg)
            covered.update(range(item.start_chapter, item.end_chapter + 1))
            previous = item
        if normalized_default is None and covered != set(range(1, chapter_count + 1)):
            msg = "chapter ranges leave chapters uncovered"
            raise ValueError(msg)
        return cls(default_voice=normalized_default, ranges=ordered)


@dataclass(frozen=True)
class EpubRequest:
    """Job request for EPUB input."""

    source_path: str
    source_language: str
    target_language: str
    voice: str | None = None
    voice_map: tuple[VoiceRange, ...] = ()


@dataclass(frozen=True)
class AudioRequest:
    """Job request for audio input."""

    source_path: str
    source_language: str
    target_language: str
    asr_model: str | None = None
    voice: str | None = None


type JobRequest = EpubRequest | AudioRequest


@dataclass(frozen=True)
class Chunk:
    """A text segment produced by the chunking engine."""

    chapter_id: str
    sequence_id: int
    text: str
