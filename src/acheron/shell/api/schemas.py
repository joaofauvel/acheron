"""Pydantic models for API request validation."""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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


class VoiceRangeRequest(_StrictRequest):
    """Strict wire representation of an inclusive chapter voice range."""

    start_chapter: int
    end_chapter: int
    voice: str


class SubmitJobRequest(_StrictRequest):
    """Request body for job submission."""

    source_type: str
    source_path: str
    source_language: str
    target_language: str
    executor_strategy: str = "streaming"
    asr_model: str | None = None
    label: str | None = None
    voice: str | None = None
    voice_map: list[VoiceRangeRequest] = Field(default_factory=list)
    input_id: str | None = None


class RetryJobRequest(_StrictRequest):
    """Optional overrides for a fresh retry submission."""

    source_path: str | None = None
    source_language: str | None = None
    target_language: str | None = None
    executor_strategy: str | None = None
    asr_model: str | None = None
    label: str | None = None
    voice: str | None = None
    voice_map: list[VoiceRangeRequest] | None = None


class ResumeJobRequest(_StrictRequest):
    """Targeted cache invalidation selections for plan resume."""

    invalidate_steps: list[str] = Field(default_factory=list)
    invalidate_chapters: list[int] = Field(default_factory=list)


class AdminErrorResponse(BaseModel):
    """Sanitized error returned by administrative endpoints."""

    type: str
    message: str
    remediation: str | None = None


_MAX_ADMIN_DURATION_SECONDS = 100 * 365 * 24 * 60 * 60


class _AdminDurationRequest(_StrictRequest):
    @field_validator(
        "older_than_seconds",
        "retention_seconds",
        "keep_successful_seconds",
        "keep_failed_seconds",
        check_fields=False,
        mode="before",
    )
    @classmethod
    def _validate_duration(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, bool):
            msg = "duration must be a finite non-negative number"
            raise ValueError(msg)  # noqa: TRY004 - Pydantic turns this into a 422 validation error.
        if not isinstance(value, (int, float, str)):
            raise ValueError("duration must be a finite non-negative number")  # noqa: TRY004
        try:
            numeric = float(value)
        except ValueError as exc:
            raise ValueError("duration must be a finite non-negative number") from exc
        if not math.isfinite(numeric) or not 0 <= numeric <= _MAX_ADMIN_DURATION_SECONDS:
            msg = "duration must be finite, non-negative, and within the supported range"
            raise ValueError(msg)
        return value


class ReapStaleRequest(_AdminDurationRequest):
    """Request body for stale-job reaping."""

    older_than_seconds: float
    reason: str


class MarkFailedRequest(_StrictRequest):
    """Request body for marking a job failed."""

    reason: str


class ArchiveRequest(_StrictRequest):
    """Request body for archiving jobs."""

    reason: str | None = None


class CleanupRequest(_AdminDurationRequest):
    """Request body for retention cleanup."""

    keep_successful_seconds: float | None = Field(default=None, gt=0)
    keep_failed_seconds: float | None = Field(default=None, gt=0)
    retention_seconds: float | None = Field(default=None, gt=0)
    apply: bool = False
    reason: str | None = None

    @model_validator(mode="after")
    def _require_retention_windows(self) -> CleanupRequest:
        if self.retention_seconds is None and (
            self.keep_successful_seconds is None or self.keep_failed_seconds is None
        ):
            raise ValueError("keep_successful_seconds and keep_failed_seconds are required")
        return self


class CleanupCandidateResponse(BaseModel):
    """Public cleanup candidate."""

    job_id: str
    status: str
    archived: bool = False
    relative_paths: list[str]
    reclaimable_bytes: int


class CleanupFailureResponse(BaseModel):
    """Public cleanup failure."""

    job_id: str
    relative_paths: list[str]
    message: str


class CleanupResponse(BaseModel):
    """Public cleanup preview or application report."""

    apply: bool
    candidates: list[CleanupCandidateResponse]
    deleted_job_ids: list[str]
    failures: list[CleanupFailureResponse]
    deleted_count: int
    deleted_bytes: int
    reclaimable_bytes: int


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
    "AdminErrorResponse",
    "ArchiveRequest",
    "CapabilitiesResponse",
    "CleanupCandidateResponse",
    "CleanupFailureResponse",
    "CleanupRequest",
    "CleanupResponse",
    "ErrorResponse",
    "InputResponse",
    "JobListResponse",
    "JobLogEvent",
    "JobProgress",
    "JobResponse",
    "LanguagePair",
    "MarkFailedRequest",
    "OutputSummary",
    "PlanResponse",
    "PlanStepResponse",
    "ReapStaleRequest",
    "ResumeJobRequest",
    "RetryJobRequest",
    "StepError",
    "SubmitJobRequest",
    "VoiceRangeRequest",
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
