"""Pydantic models for API request validation."""

from pydantic import BaseModel, ConfigDict, Field

from acheron.core.models import JsonValue  # noqa: TC001
from acheron.core.schemas import (
    CapabilitiesResponse,
    ErrorResponse,
    InputResponse,
    JobListResponse,
    JobLogEvent,
    JobProgress,
    JobResponse,
    LanguagePair,
    OutputSummary,
    PlanResponse,
    PlanStepResponse,
    StepError,
    WorkerCapability,
    WorkerListResponse,
    WorkerResponse,
)


class _StrictRequest(BaseModel):
    """Request body: reject unknown fields so client typos fail loudly."""

    model_config = ConfigDict(extra="forbid")


class SubmitJobRequest(_StrictRequest):
    """Request body for job submission."""

    source_type: str
    source_path: str
    source_language: str
    target_language: str
    executor_strategy: str = "streaming"
    asr_model: str | None = None
    label: str | None = None


class RetryJobRequest(_StrictRequest):
    """Optional overrides for a fresh retry submission."""

    source_path: str | None = None
    source_language: str | None = None
    target_language: str | None = None
    executor_strategy: str | None = None
    asr_model: str | None = None
    label: str | None = None


class ResumeJobRequest(_StrictRequest):
    """Targeted cache invalidation selections for plan resume."""

    invalidate_steps: list[str] = Field(default_factory=list)
    invalidate_chapters: list[int] = Field(default_factory=list)


class WorkerCapabilitiesRequest(_StrictRequest):
    """Worker capabilities in a registration request."""

    worker_type: str
    supported_languages_in: list[str]
    supported_languages_out: list[str]
    supported_formats_in: list[str] = Field(default_factory=list)
    supported_formats_out: list[str] = Field(default_factory=list)
    max_payload_bytes: int | None = None
    batch_capable: bool = False
    model_source: str | None = None
    max_input_tokens: int | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


__all__ = [
    "CapabilitiesResponse",
    "ErrorResponse",
    "InputResponse",
    "JobListResponse",
    "JobLogEvent",
    "JobProgress",
    "JobResponse",
    "LanguagePair",
    "OutputSummary",
    "PlanResponse",
    "PlanStepResponse",
    "ResumeJobRequest",
    "RetryJobRequest",
    "StepError",
    "SubmitJobRequest",
    "WorkerCapabilitiesRequest",
    "WorkerCapability",
    "WorkerListResponse",
    "WorkerRegistrationRequest",
    "WorkerResponse",
]


class WorkerRegistrationRequest(_StrictRequest):
    """Request body for worker registration."""

    worker_id: str
    endpoint: str
    transport: str
    capabilities: WorkerCapabilitiesRequest
