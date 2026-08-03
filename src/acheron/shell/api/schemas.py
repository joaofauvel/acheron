"""Pydantic models for API request validation."""

from __future__ import annotations

import math
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from acheron.core.models import JsonValue  # noqa: TC001
from acheron.core.schemas import (
    CapabilitiesResponse,
    CertificateReloadResponse,
    CertificateStatusResponse,
    CleanupCandidateResponse,
    CleanupFailureResponse,
    CleanupResponse,
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
    RegistrationTokenAuditResponse,
    RegistrationTokenRolloutResponse,
    RegistrationTokenRotationResponse,
    RegistrationTokenStatusResponse,
    StepError,
    WorkerCapability,
    WorkerListResponse,
    WorkerResponse,
)


class _StrictRequest(BaseModel):
    """Request body: reject unknown fields so client typos fail loudly."""

    model_config = ConfigDict(extra="forbid")


_CONTROL_CHARACTER_LIMIT = 32
_DELETE_CHARACTER = 127


def _validate_voice_text(value: str | None) -> str | None:
    if value is not None and any(
        ord(char) < _CONTROL_CHARACTER_LIMIT or ord(char) == _DELETE_CHARACTER for char in value
    ):
        raise ValueError("voice contains control characters")
    return value


class VoiceRangeRequest(_StrictRequest):
    """Strict wire representation of an inclusive chapter voice range."""

    start_chapter: int
    end_chapter: int
    voice: str = Field(max_length=128)

    _voice_is_safe = field_validator("voice")(_validate_voice_text)


class SubmitJobRequest(_StrictRequest):
    """Request body for job submission."""

    source_type: str
    source_path: str
    source_language: str
    target_language: str
    executor_strategy: str = "streaming"
    asr_model: str | None = None
    label: str | None = None
    voice: str | None = Field(default=None, max_length=128)
    voice_map: list[VoiceRangeRequest] = Field(default_factory=list, max_length=128)
    input_id: str | None = None

    _voice_is_safe = field_validator("voice")(_validate_voice_text)


class RetryJobRequest(_StrictRequest):
    """Optional overrides for a fresh retry submission."""

    source_path: str | None = None
    source_language: str | None = None
    target_language: str | None = None
    executor_strategy: str | None = None
    asr_model: str | None = None
    label: str | None = None
    voice: str | None = Field(default=None, max_length=128)
    voice_map: list[VoiceRangeRequest] | None = Field(default=None, max_length=128)

    _voice_is_safe = field_validator("voice")(_validate_voice_text)


class ResumeJobRequest(_StrictRequest):
    """Targeted cache invalidation selections for plan resume."""

    invalidate_steps: list[str] = Field(default_factory=list)
    invalidate_chapters: list[int] = Field(default_factory=list)


class AdminErrorResponse(BaseModel):
    """Sanitized error returned by administrative endpoints."""

    type: str
    message: str
    remediation: str | None = None


class TokenRotateRequest(_StrictRequest):
    """Request body for a registration-token rotation."""

    reason: str = Field(min_length=1, max_length=512)


_MAX_ADMIN_DURATION_SECONDS = 100 * 365 * 24 * 60 * 60
_MAX_CAPABILITY_METADATA_STRING = 256
_MAX_CAPABILITY_METADATA_ITEMS = 128
_MAX_CAPABILITY_METADATA_KEYS = 64
_MAX_CAPABILITY_METADATA_DEPTH = 4


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
        except (OverflowError, ValueError) as exc:
            raise ValueError("duration must be a finite non-negative number") from exc
        if not math.isfinite(numeric) or not 0 <= numeric <= _MAX_ADMIN_DURATION_SECONDS:
            msg = "duration must be finite, non-negative, and within the supported range"
            raise ValueError(msg)
        return value


class ReapStaleRequest(_AdminDurationRequest):
    """Request body for stale-job reaping."""

    older_than_seconds: float
    reason: str = Field(max_length=512)


class MarkFailedRequest(_StrictRequest):
    """Request body for marking a job failed."""

    reason: str = Field(max_length=512)


class ArchiveRequest(_StrictRequest):
    """Request body for archiving jobs."""

    reason: str | None = Field(default=None, max_length=512)


class CleanupRequest(_AdminDurationRequest):
    """Request body for retention cleanup."""

    keep_successful_seconds: float | None = Field(default=None, gt=0)
    keep_failed_seconds: float | None = Field(default=None, gt=0)
    retention_seconds: float | None = Field(default=None, gt=0)
    apply: StrictBool = False
    reason: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def _require_retention_windows(self) -> CleanupRequest:
        if self.retention_seconds is None and (
            self.keep_successful_seconds is None or self.keep_failed_seconds is None
        ):
            raise ValueError("keep_successful_seconds and keep_failed_seconds are required")
        return self


_MAX_CAPABILITY_PAYLOAD_BYTES = 1 << 40
_MAX_CAPABILITY_INPUT_TOKENS = 1_000_000_000


class WorkerCapabilitiesRequest(_StrictRequest):
    """Worker capabilities in a registration request."""

    worker_type: str
    supported_languages_in: list[Annotated[str, Field(max_length=64)]] = Field(max_length=128)
    supported_languages_out: list[Annotated[str, Field(max_length=64)]] = Field(max_length=128)
    supported_formats_in: list[Annotated[str, Field(max_length=64)]] = Field(default_factory=list, max_length=128)
    supported_formats_out: list[Annotated[str, Field(max_length=64)]] = Field(default_factory=list, max_length=128)
    max_payload_bytes: int | None = Field(default=None, ge=0, le=_MAX_CAPABILITY_PAYLOAD_BYTES)
    batch_capable: bool = False
    model_source: str | None = Field(default=None, max_length=256)
    max_input_tokens: int | None = Field(default=None, ge=0, le=_MAX_CAPABILITY_INPUT_TOKENS)
    metadata: dict[str, JsonValue] = Field(default_factory=dict, max_length=64)

    @field_validator("metadata")
    @classmethod
    def _validate_metadata_values(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        def validate(item: JsonValue, depth: int = 0) -> None:
            if depth > _MAX_CAPABILITY_METADATA_DEPTH:
                raise ValueError("metadata nesting must be at most 4 levels")
            match item:
                case str() if len(item) > _MAX_CAPABILITY_METADATA_STRING:
                    raise ValueError("metadata string values must be at most 256 characters")
                case list() if len(item) > _MAX_CAPABILITY_METADATA_ITEMS:
                    raise ValueError("metadata collections must contain at most 128 items")
                case dict() if len(item) > _MAX_CAPABILITY_METADATA_KEYS:
                    raise ValueError("metadata mappings must contain at most 64 items")
                case list() as items:
                    for nested in items:
                        validate(nested, depth + 1)
                case dict() as mapping:
                    for nested in mapping.values():
                        validate(nested, depth + 1)
                case _:
                    return

        validate(value)
        return value


__all__ = [
    "AdminErrorResponse",
    "ArchiveRequest",
    "CapabilitiesResponse",
    "CertificateReloadResponse",
    "CertificateStatusResponse",
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
    "RegistrationTokenAuditResponse",
    "RegistrationTokenRolloutResponse",
    "RegistrationTokenRotationResponse",
    "RegistrationTokenStatusResponse",
    "ResumeJobRequest",
    "RetryJobRequest",
    "StepError",
    "SubmitJobRequest",
    "TokenRotateRequest",
    "VoiceRangeRequest",
    "WorkerCapabilitiesRequest",
    "WorkerCapability",
    "WorkerListResponse",
    "WorkerRegistrationRequest",
    "WorkerResponse",
]


class WorkerRegistrationRequest(_StrictRequest):
    """Request body for worker registration."""

    worker_id: str = Field(max_length=128)
    endpoint: str = Field(max_length=2048)
    transport: str = Field(max_length=64)
    capabilities: WorkerCapabilitiesRequest

    @field_validator("endpoint")
    @classmethod
    def _validate_endpoint(cls, value: str) -> str:
        if any(ord(char) < _CONTROL_CHARACTER_LIMIT or ord(char) == _DELETE_CHARACTER for char in value):
            raise ValueError("endpoint contains control characters")
        if any(char.isspace() for char in value) or "\\\\" in value:
            raise ValueError("endpoint contains invalid characters")
        parsed = urlsplit(value)
        if parsed.scheme.casefold() not in {"http", "https", "grpc", "grpcs"} or not parsed.hostname:
            raise ValueError("endpoint must be an http(s) or grpc(s) URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("endpoint userinfo is not allowed")
        if parsed.query or parsed.fragment or any(part == ".." for part in parsed.path.split("/")):
            raise ValueError("endpoint path or query is not allowed")
        return value
