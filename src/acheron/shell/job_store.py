"""Tracked job record used by the job store."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from acheron.core.models import PlanStatus

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
    status: PlanStatus = PlanStatus.PENDING
