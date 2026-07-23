"""Pydantic models for API request validation."""

from pydantic import BaseModel, ConfigDict, Field

from acheron.core.models import JsonValue  # noqa: TC001
from acheron.core.schemas import (
    CapabilitiesResponse,
    JobListResponse,
    JobResponse,
    LanguagePair,
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
    "JobListResponse",
    "JobResponse",
    "LanguagePair",
    "SubmitJobRequest",
    "WorkerCapabilitiesRequest",
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
