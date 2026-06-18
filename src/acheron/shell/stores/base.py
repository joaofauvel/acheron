"""Abstract base classes for orchestrator state storage."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from acheron.core.models import WorkerCapabilities, WorkerType
    from acheron.shell.job_store import TrackedJob
    from acheron.shell.registry import RegisteredWorker


class WorkerStore(ABC):
    """Persistent or in-memory store of registered workers and their health state."""

    max_failures: int = 3

    @abstractmethod
    def register(
        self,
        worker_id: str,
        endpoint: str,
        transport: str,
        capabilities: WorkerCapabilities,
        metadata: dict[str, object] | None = None,
    ) -> None: ...

    @abstractmethod
    def unregister(self, worker_id: str) -> None: ...

    @abstractmethod
    def get(self, worker_id: str) -> RegisteredWorker | None: ...

    @abstractmethod
    def list_all(self) -> tuple[RegisteredWorker, ...]: ...

    @abstractmethod
    def find_by_type(self, worker_type: WorkerType) -> tuple[RegisteredWorker, ...]: ...

    @abstractmethod
    def find_by_language(self, src: str, dst: str) -> tuple[RegisteredWorker, ...]: ...

    @abstractmethod
    def record_health_failure(self, worker_id: str) -> bool:
        """Record a failed health check. Returns True if the worker was removed."""
        ...

    @abstractmethod
    def record_health_success(self, worker_id: str) -> None: ...

    @abstractmethod
    def close(self) -> None:
        """Release any resources held by the store (Redis pools, file handles)."""
        ...


class JobStore(ABC):
    """Persistent or in-memory store of tracked jobs."""

    @abstractmethod
    def put(self, job: TrackedJob) -> None: ...

    @abstractmethod
    def get(self, job_id: str) -> TrackedJob | None: ...

    @abstractmethod
    def list_all(self) -> tuple[TrackedJob, ...]: ...

    @abstractmethod
    def close(self) -> None: ...
