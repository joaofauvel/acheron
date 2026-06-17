"""In-memory job tracking store."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from acheron.core.models import ExecutorStrategy, JobRequest, Plan, PlanResult


@dataclass
class TrackedJob:
    """A job tracked through its lifecycle."""

    job_id: str
    request: JobRequest
    strategy: ExecutorStrategy
    plan: Plan | None = None
    result: PlanResult | None = None
    status: str = "pending"


class JobStore:
    """In-memory store for tracked jobs."""

    def __init__(self) -> None:
        self._jobs: dict[str, TrackedJob] = {}

    def put(self, job: TrackedJob) -> None:
        """Store or update a tracked job."""
        self._jobs[job.job_id] = job

    def get(self, job_id: str) -> TrackedJob | None:
        """Retrieve a job by ID."""
        return self._jobs.get(job_id)

    def list_all(self) -> tuple[TrackedJob, ...]:
        """Return all tracked jobs."""
        return tuple(self._jobs.values())
