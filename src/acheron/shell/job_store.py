"""Tracked job record used by the job store."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from acheron.core.models import ExecutorStrategy, PlanStatus, WorkerType

if TYPE_CHECKING:
    from acheron.core.models import JobRequest, Plan, PlanResult


@dataclass
class JobProgressState:
    """Mutable execution progress retained with a tracked job."""

    completed_steps: int = 0
    total_steps: int = 0
    current_step_id: str | None = None
    current_worker_type: WorkerType | None = None
    current_worker_id: str | None = None
    eta_seconds: float | None = None
    successful_duration_seconds: float = 0.0


def _normalise_utc(value: datetime) -> datetime:
    """Require a timezone-aware timestamp and normalize it to UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        msg = "lifecycle timestamps must be timezone-aware"
        raise ValueError(msg)
    return value.astimezone(UTC)


@dataclass
class TrackedJob:
    """A job tracked through its lifecycle."""

    job_id: str
    request: JobRequest
    strategy: ExecutorStrategy
    label: str | None = None
    retries_from: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_persisted_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    progress: JobProgressState = field(default_factory=JobProgressState)
    plan: Plan | None = None
    result: PlanResult | None = None
    status: PlanStatus = PlanStatus.PENDING

    def __post_init__(self) -> None:
        self.created_at = _normalise_utc(self.created_at)
        self.last_persisted_at = _normalise_utc(self.last_persisted_at)
