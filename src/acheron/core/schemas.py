"""Wire-format response schemas shared between the Acheron client and server."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

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


class OutputSummary(BaseModel):
    """Operator-relevant metadata for a produced artifact."""

    download_url: str
    filename: str
    size_bytes: int
    content_type: str


class CostEstimateResponse(BaseModel):
    """Execution-time cost estimate and pricing provenance."""

    cost: float | None
    basis: CostBasis
    rate_per_hour: float | None = None
    gpu_type: str | None = None
    secure_cloud: bool | None = None
    queried_at: datetime | None = None
    cache_age_seconds: float | None = Field(default=None, ge=0)

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
    gpu_seconds: float | None
    cost: float | None
    basis: CostBasis
    rate_per_hour: float | None = None
    gpu_type: str | None = None
    secure_cloud: bool | None = None
    queried_at: datetime | None = None
    cache_age_seconds: float | None = Field(default=None, ge=0)

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
    total_cost: float
    total_cost_basis: CostBasis | None
    cost_breakdown: list[CostBreakdownResponse]


class CostSummaryResponse(BaseModel):
    """Aggregated execution-time estimates for a selected window."""

    window: str
    since: datetime | None
    until: datetime
    total_cost: float
    job_count: int
    unknown_cost_jobs: int

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
    executor_strategy: ExecutorStrategy
    created_at: datetime
    last_persisted_at: datetime
    archived_at: datetime | None = None
    progress: JobProgress
    total_cost: float
    total_duration_seconds: float
    total_cost_basis: CostBasis | None
    outputs: list[OutputSummary]
    errors: list[StepError]
    warnings: list[str]

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


class WorkerResponse(BaseModel):
    """Response for a single worker."""

    worker_id: str
    endpoint: str
    transport: str
    worker_type: str
    consecutive_failures: int
    status: WorkerStatus = WorkerStatus.HEALTHY
    booting_elapsed_seconds: float | None = None
    booting_timeout_seconds: float = 600.0
    last_error: str | None = None
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


class InputResponse(BaseModel):
    """Response for a successful upload."""

    source_path: str
    filename: str
    size_bytes: int
    content_type: str | None = None


class WorkerCapability(BaseModel):
    """Capability descriptor for a registered worker."""

    worker_id: str
    worker_type: str
    model_source: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class PlanStepResponse(BaseModel):
    """Public structure for one planned pipeline step."""

    step_id: str
    worker_type: WorkerType
    depends_on: list[str]
    status: StepStatus


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
            source_language=plan.source_language,
            target_language=plan.target_language,
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
    "StepError",
    "WorkerCapability",
    "WorkerListResponse",
    "WorkerResponse",
]
