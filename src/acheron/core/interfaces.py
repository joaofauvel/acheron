"""Abstract interfaces for workers and executors."""

from abc import ABC, abstractmethod

from acheron.core.models import (
    BatchJob,
    BatchStatus,
    Job,
    JobResult,
    Plan,
    PlanResult,
    WorkerCapabilities,
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


class StreamingWorker(Worker, ABC):
    """Worker extension supporting batch submission for GPU throughput."""

    @abstractmethod
    async def submit_batch(self, batch: BatchJob) -> str:
        """Submit a batch of jobs, returning a batch handle."""
        ...

    @abstractmethod
    async def poll_batch(self, batch_handle: str) -> BatchStatus:
        """Check progress of a submitted batch."""
        ...

    @abstractmethod
    async def collect_results(self, batch_handle: str) -> tuple[JobResult, ...]:
        """Pull all completed results from a batch."""
        ...


class Executor(ABC):
    """Interface for plan execution strategies."""

    @abstractmethod
    async def run(self, plan: Plan) -> PlanResult:
        """Execute a plan and return the aggregated result."""
        ...
