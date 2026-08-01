"""Wire-format response schemas shared between the Acheron client and server."""

import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, FiniteFloat, field_validator

from acheron.core.errors import sanitise_public_message
from acheron.core.models import (
    CostBasis,
    ExecutorStrategy,
    JsonValue,
    Plan,
    PlanStatus,
    StepStatus,
    WorkerStatus,
    WorkerType,
)

_MAX_PUBLIC_VOICE_LENGTH = 128
_CONTROL_CHARACTER_LIMIT = 32
_DELETE_CHARACTER = 127


class OutputSummary(BaseModel):
    """Operator-relevant metadata for a produced artifact."""

    download_url: str
    filename: str
    size_bytes: int = Field(ge=0)
    content_type: str


class CostEstimateResponse(BaseModel):
    """Execution-time cost estimate and pricing provenance."""

    cost: FiniteFloat | None = Field(default=None, ge=0)
    basis: CostBasis
    rate_per_hour: FiniteFloat | None = Field(default=None, ge=0)
    gpu_type: str | None = None
    secure_cloud: bool | None = None
    queried_at: datetime | None = None
    cache_age_seconds: FiniteFloat | None = Field(default=None, ge=0)

    @field_validator("queried_at")
    @classmethod
    def _require_utc_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            msg = "queried_at must be timezone-aware"
            raise ValueError(msg)
        return value.astimezone(UTC)


class CostBreakdownResponse(BaseModel):
    """Flattened cost evidence for one executed plan step."""

    step_id: str
    worker_type: WorkerType
    worker_id: str | None
    gpu_seconds: FiniteFloat | None = Field(default=None, ge=0)
    cost: FiniteFloat | None = Field(default=None, ge=0)
    basis: CostBasis
    rate_per_hour: FiniteFloat | None = Field(default=None, ge=0)
    gpu_type: str | None = None
    secure_cloud: bool | None = None
    queried_at: datetime | None = None
    cache_age_seconds: FiniteFloat | None = Field(default=None, ge=0)

    @field_validator("queried_at")
    @classmethod
    def _require_utc_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            msg = "queried_at must be timezone-aware"
            raise ValueError(msg)
        return value.astimezone(UTC)


class JobCostResponse(BaseModel):
    """Execution-time cost evidence for one tracked job."""

    job_id: str
    total_cost: FiniteFloat = Field(ge=0)
    total_cost_basis: CostBasis | None
    cost_breakdown: list[CostBreakdownResponse]


class CostSummaryResponse(BaseModel):
    """Aggregated execution-time estimates for a selected window."""

    window: str
    since: datetime | None
    until: datetime
    total_cost: FiniteFloat = Field(ge=0)
    job_count: int = Field(ge=0)
    unknown_cost_jobs: int = Field(ge=0)

    @field_validator("since", "until")
    @classmethod
    def _require_utc_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            msg = "cost summary timestamps must be timezone-aware"
            raise ValueError(msg)
        return value.astimezone(UTC)


class CostWindowQuery(BaseModel):
    """Supported cost summary windows."""

    window: Literal["24h", "7d", "30d", "all"] = "7d"


class StepError(BaseModel):
    """Public failure attribution for one execution step."""

    step_id: str | None
    worker_type: WorkerType | None
    worker_id: str | None
    message: str
    timestamp: datetime


class JobProgress(BaseModel):
    """Current aggregate and step-level execution progress."""

    completed_steps: int = 0
    total_steps: int = 0
    current_step_id: str | None = None
    current_worker_type: WorkerType | None = None
    current_worker_id: str | None = None
    eta_seconds: float | None = None


class JobLogEvent(BaseModel):
    """One newline-delimited progress event."""

    job_id: str
    timestamp: datetime
    status: PlanStatus
    step_id: str | None = None
    worker_type: WorkerType | None = None
    worker_id: str | None = None
    progress: JobProgress
    message: str


class ErrorResponse(BaseModel):
    """Structured domain error returned by the API."""

    type: str
    message: str
    remediation: str | None = None


class JobResponse(BaseModel):
    """Complete operator-facing response for a tracked job."""

    job_id: str
    status: PlanStatus
    plan_id: str | None
    label: str | None
    retries_from: str | None
    source_type: str
    source_language: str
    target_language: str
    asr_model: str | None
    voice: str | None = Field(default=None, max_length=_MAX_PUBLIC_VOICE_LENGTH)
    voice_map: list[dict[str, str | int]] = Field(default_factory=list, max_length=128)
    executor_strategy: ExecutorStrategy
    created_at: datetime
    last_persisted_at: datetime
    archived_at: datetime | None = None
    progress: JobProgress
    total_cost: FiniteFloat = Field(ge=0)
    total_duration_seconds: FiniteFloat = Field(ge=0)
    total_cost_basis: CostBasis | None
    outputs: list[OutputSummary]
    errors: list[StepError]
    warnings: list[str]

    @field_validator("voice")
    @classmethod
    def _validate_voice(cls, value: str | None) -> str | None:
        if value is not None and any(
            ord(char) < _CONTROL_CHARACTER_LIMIT or ord(char) == _DELETE_CHARACTER for char in value
        ):
            raise ValueError("voice contains control characters")
        return value

    @field_validator("voice_map")
    @classmethod
    def _validate_voice_map(cls, value: list[dict[str, str | int]]) -> list[dict[str, str | int]]:
        for item in value:
            voice = item.get("voice")
            if isinstance(voice, str) and (
                len(voice) > _MAX_PUBLIC_VOICE_LENGTH
                or any(ord(char) < _CONTROL_CHARACTER_LIMIT or ord(char) == _DELETE_CHARACTER for char in voice)
            ):
                raise ValueError("voice map values must be bounded and control-free")
        return value

    @field_validator("created_at", "last_persisted_at", "archived_at")
    @classmethod
    def _require_utc_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            msg = "lifecycle timestamps must be timezone-aware"
            raise ValueError(msg)
        return value.astimezone(UTC)


class JobListResponse(BaseModel):
    """Response for listing jobs."""

    jobs: list[JobResponse]


class ReapStaleResponse(BaseModel):
    """Response for stale-job reaping."""

    reaped: int
    job_ids: list[str] = Field(max_length=1000)


class AdminJobResponse(BaseModel):
    """Response for an administrative job mutation."""

    job: JobResponse


class WorkerErrorEventResponse(BaseModel):
    """Public projection of one worker health failure."""

    timestamp: datetime
    message: str
    consecutive_failures: int

    @field_validator("timestamp")
    @classmethod
    def _require_utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            msg = "worker error timestamps must be timezone-aware"
            raise ValueError(msg)
        return value.astimezone(UTC)


class WorkerRegistrationResponse(BaseModel):
    """Minimal response returned after worker registration."""

    worker_id: str
    status: WorkerStatus = WorkerStatus.HEALTHY


class WorkerResponse(BaseModel):
    """Sanitized response for a single worker."""

    worker_id: str
    endpoint: str | None = None
    transport: str = ""
    worker_type: str
    consecutive_failures: int
    status: WorkerStatus = WorkerStatus.HEALTHY
    booting_elapsed_seconds: float | None = None
    booting_timeout_seconds: float = 600.0
    last_error: str | None = None
    error_history: list[WorkerErrorEventResponse] = Field(default_factory=list, max_length=10)
    max_input_tokens: int | None = None


class WorkerListResponse(BaseModel):
    """Response for listing workers."""

    workers: list[WorkerResponse]


class LanguagePair(BaseModel):
    """A supported source→target language pair."""

    src: str
    dst: str
    workers: list[str]


class CapabilitiesResponse(BaseModel):
    """Response for capability discovery."""

    language_pairs: list[LanguagePair]
    workers: list[WorkerCapability] = Field(default_factory=list)


class VersionResponse(BaseModel):
    """Public package and deployment identity."""

    version: str
    sha: str | None = None
    build_time: datetime | None = None
    branch: str | None = None
    dirty: bool | None = None
    image: str | None = None
    registry: str | None = None

    @field_validator("build_time")
    @classmethod
    def _require_utc_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            msg = "build_time must be timezone-aware"
            raise ValueError(msg)
        return value.astimezone(UTC)


class InputResponse(BaseModel):
    """Response for a successful upload."""

    input_id: str = ""
    source_path: str
    filename: str
    size_bytes: int
    content_type: str | None = None


class WorkerCapability(BaseModel):
    """Allowlisted capability descriptor for a registered worker."""

    worker_id: str
    worker_type: str
    supported_languages_in: list[str] = Field(default_factory=list)
    supported_languages_out: list[str] = Field(default_factory=list)
    supported_formats_in: list[str] = Field(default_factory=list)
    supported_formats_out: list[str] = Field(default_factory=list)
    max_payload_bytes: int | None = None
    max_input_tokens: int | None = None
    batch_capable: bool = False
    speakers: list[str] = Field(default_factory=list)
    model_source: str | None = Field(default=None, exclude=True)
    metadata: dict[str, JsonValue] = Field(default_factory=dict, exclude=True)


class PlanStepResponse(BaseModel):
    """Public structure for one planned pipeline step."""

    step_id: str
    worker_type: WorkerType
    depends_on: list[str]
    status: StepStatus


_SAFE_LANGUAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,31}$")


def _public_language(value: object) -> str:
    """Return a bounded language code for public plan responses."""
    if not isinstance(value, str) or _SAFE_LANGUAGE_RE.fullmatch(value) is None:
        return "unknown"
    if sanitise_public_message(value, fallback="unknown") == "unknown":
        return "unknown"
    return value


class PlanResponse(BaseModel):
    """Public operator-facing representation of a compiled plan."""

    plan_id: str
    job_id: str
    source_type: str
    source_language: str
    target_language: str
    executor_strategy: ExecutorStrategy
    steps: list[PlanStepResponse]

    @classmethod
    def from_plan(cls, plan: Plan) -> PlanResponse:
        """Convert a compiled Plan into its public response shape."""
        return cls(
            plan_id=plan.plan_id,
            job_id=plan.job_id,
            source_type=plan.source_type,
            source_language=_public_language(plan.source_language),
            target_language=_public_language(plan.target_language),
            executor_strategy=plan.executor_strategy,
            steps=[
                PlanStepResponse(
                    step_id=step.step_id,
                    worker_type=step.type,
                    depends_on=list(step.depends_on),
                    status=step.status,
                )
                for step in plan.steps
            ],
        )


__all__ = [
    "AdminJobResponse",
    "CapabilitiesResponse",
    "CostBreakdownResponse",
    "CostEstimateResponse",
    "CostSummaryResponse",
    "CostWindowQuery",
    "ErrorResponse",
    "InputResponse",
    "JobCostResponse",
    "JobListResponse",
    "JobLogEvent",
    "JobProgress",
    "JobResponse",
    "LanguagePair",
    "OutputSummary",
    "PlanResponse",
    "PlanStepResponse",
    "ReapStaleResponse",
    "StepError",
    "VersionResponse",
    "WorkerCapability",
    "WorkerErrorEventResponse",
    "WorkerListResponse",
    "WorkerRegistrationResponse",
    "WorkerResponse",
]
