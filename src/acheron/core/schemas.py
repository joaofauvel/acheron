"""Wire-format response schemas shared between the Acheron client and server."""

from pydantic import BaseModel, Field

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
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


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
