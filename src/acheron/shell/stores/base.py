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

    async def connect(self) -> None:
        """Verify the backend is reachable. No-op for stores without a remote backend."""

    @abstractmethod
    async def register(
        self,
        worker_id: str,
        endpoint: str,
        transport: str,
        capabilities: WorkerCapabilities,
        metadata: dict[str, object] | None = None,
    ) -> None:
        """Register a new worker or re-register an existing one."""
        ...

    @abstractmethod
    async def unregister(self, worker_id: str) -> None:
        """Remove a worker from the store."""
        ...

    @abstractmethod
    async def get(self, worker_id: str) -> RegisteredWorker | None:
        """Look up a worker by ID."""
        ...

    @abstractmethod
    async def list_all(self) -> tuple[RegisteredWorker, ...]:
        """Return all registered workers."""
        ...

    @abstractmethod
    async def find_by_type(self, worker_type: WorkerType) -> tuple[RegisteredWorker, ...]:
        """Find workers matching a given WorkerType."""
        ...

    @abstractmethod
    async def find_by_language(self, src: str, dst: str) -> tuple[RegisteredWorker, ...]:
        """Find workers supporting a source→target language pair."""
        ...

    @abstractmethod
    async def record_health_failure(self, worker_id: str) -> bool:
        """Record a failed health check. Returns True if the worker was removed."""
        ...

    @abstractmethod
    async def record_health_success(self, worker_id: str) -> None:
        """Record a successful health check, resetting the failure counter."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Release any resources held by the store (Redis pools, file handles)."""
        ...


class JobStore(ABC):
    """Persistent or in-memory store of tracked jobs."""

    async def connect(self) -> None:
        """Verify the backend is reachable. No-op for stores without a remote backend."""

    @abstractmethod
    async def put(self, job: TrackedJob) -> None:
        """Store or update a tracked job."""
        ...

    @abstractmethod
    async def get(self, job_id: str) -> TrackedJob | None:
        """Retrieve a tracked job by ID."""
        ...

    @abstractmethod
    async def list_all(self) -> tuple[TrackedJob, ...]:
        """Return all tracked jobs."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Release any resources held by the store."""
        ...
