"""Wire-format response schemas shared between the Acheron client and server."""

from pydantic import BaseModel

from acheron.core.models import (
    CostBasis,
    PlanStatus,
    WorkerStatus,
)


class JobResponse(BaseModel):
    """Response for a single job."""

    job_id: str
    status: PlanStatus
    plan_id: str | None = None
    completed_steps: int = 0
    total_steps: int = 0
    total_cost: float = 0.0
    total_duration_seconds: float = 0.0
    total_cost_basis: CostBasis | None = None
    errors: list[str] = []


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
