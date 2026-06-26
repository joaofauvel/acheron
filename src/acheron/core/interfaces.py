"""Abstract interfaces for workers and executors."""

from abc import ABC, abstractmethod

from acheron.core.models import (
    Job,
    JobResult,
    Plan,
    PlanResult,
    WorkerCapabilities,
    WorkerStatus,
)


class Worker(ABC):
    """Base interface for all compute workers."""

    @abstractmethod
    async def capabilities(self) -> WorkerCapabilities:
        """Return this worker's supported types, languages, and formats."""
        ...

    @abstractmethod
    async def execute(self, job: Job) -> JobResult:
        """Execute a single job and return the result."""
        ...

    @abstractmethod
    async def health(self) -> bool:
        """Check if this worker is reachable and ready."""
        ...


class Executor(ABC):
    """Interface for plan execution strategies."""

    @abstractmethod
    async def run(self, plan: Plan) -> PlanResult:
        """Execute a plan and return the aggregated result."""
        ...


class HealthProvider(ABC):
    """Query a hosting platform API to determine if a worker is booting or offline."""

    @abstractmethod
    async def check_status(self, endpoint_id: str) -> WorkerStatus:
        """Query the platform to verify if the container is booting vs offline."""
        ...
