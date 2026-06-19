"""Pydantic models for API request/response serialization."""

from typing import Any

from pydantic import BaseModel


class SubmitJobRequest(BaseModel):
    """Request body for job submission."""

    source_type: str
    source_path: str
    source_language: str
    target_language: str
    executor_strategy: str = "streaming"
    asr_model: str | None = None


class JobResponse(BaseModel):
    """Response for a single job."""

    job_id: str
    status: str
    plan_id: str | None = None
    completed_steps: int = 0
    total_steps: int = 0
    total_cost: float = 0.0
    total_duration_seconds: float = 0.0
    errors: list[str] = []


class JobListResponse(BaseModel):
    """Response for listing jobs."""

    jobs: list[JobResponse]


class WorkerCapabilitiesRequest(BaseModel):
    """Worker capabilities in a registration request."""

    worker_type: str
    supported_languages_in: list[str]
    supported_languages_out: list[str]
    supported_formats_in: list[str] = []
    supported_formats_out: list[str] = []
    max_payload_bytes: int | None = None
    batch_capable: bool = False
    model_source: str | None = None
    metadata: dict[str, Any] = {}


class WorkerRegistrationRequest(BaseModel):
    """Request body for worker registration."""

    worker_id: str
    endpoint: str
    transport: str
    capabilities: WorkerCapabilitiesRequest


class WorkerResponse(BaseModel):
    """Response for a single worker."""

    worker_id: str
    endpoint: str
    transport: str
    worker_type: str
    consecutive_failures: int


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
