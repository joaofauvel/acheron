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
    @abstractmethod
    async def capabilities(self) -> WorkerCapabilities: ...

    @abstractmethod
    async def execute(self, job: Job) -> JobResult: ...

    @abstractmethod
    async def health(self) -> bool: ...


class StreamingWorker(Worker, ABC):
    @abstractmethod
    async def submit_batch(self, batch: BatchJob) -> str: ...

    @abstractmethod
    async def poll_batch(self, batch_handle: str) -> BatchStatus: ...

    @abstractmethod
    async def collect_results(self, batch_handle: str) -> tuple[JobResult, ...]: ...


class Executor(ABC):
    @abstractmethod
    async def run(self, plan: Plan) -> PlanResult: ...
