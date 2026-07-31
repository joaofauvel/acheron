"""Abstract base classes for orchestrator state storage."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from acheron.core.models import JsonValue, WorkerCapabilities, WorkerStatus, WorkerType
    from acheron.shell.job_store import JobQuery, TrackedJob
    from acheron.shell.registry import RegisteredWorker


class StoreError(RuntimeError):
    """Backend failure normalized at the store boundary."""


class WorkerStore(ABC):
    """Persistent or in-memory store of registered workers and their health state."""

    max_failures: int = 3

    async def connect(self) -> None:
        """Verify the backend is reachable. No-op for stores without a remote backend."""
        return

    @abstractmethod
    async def register(
        self,
        worker_id: str,
        endpoint: str,
        transport: str,
        capabilities: WorkerCapabilities,
        metadata: dict[str, JsonValue] | None = None,
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
        """Record a successful health check.

        Resets the failure counter to 0, sets status to HEALTHY, and clears
        last_error.
        """
        ...

    @abstractmethod
    async def set_worker_status(
        self,
        worker_id: str,
        status: WorkerStatus,
        last_error: str | None,
    ) -> None:
        """Update status and error without touching failures.

        Entering BOOTING starts or preserves its persisted timestamp. Every
        non-BOOTING transition and health success clears that timestamp.
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """Release any resources held by the store (Redis pools, file handles)."""
        ...


class JobStore(ABC):
    """Persistent or in-memory store of tracked jobs."""

    async def connect(self) -> None:
        """Verify the backend is reachable. No-op for stores without a remote backend."""
        return

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

    async def list(self, query: JobQuery | None = None, *, now: datetime | None = None) -> tuple[TrackedJob, ...]:
        """Return jobs matching a typed query."""
        from datetime import UTC, datetime, timedelta  # noqa: PLC0415

        selected = await self.list_all()
        if query is None:
            return selected
        if now is not None and (now.tzinfo is None or now.utcoffset() is None):
            msg = "now must be timezone-aware"
            raise ValueError(msg)
        reference = now.astimezone(UTC) if now is not None else datetime.now(UTC)
        cutoff = (
            reference - timedelta(seconds=query.older_than_seconds) if query.older_than_seconds is not None else None
        )
        return tuple(
            job
            for job in selected
            if (query.status is None or job.status is query.status)
            and (query.since is None or job.created_at >= query.since)
            and (query.before is None or job.created_at <= query.before)
            and (cutoff is None or job.last_persisted_at <= cutoff)
            and (query.include_archived or getattr(job, "archived_at", None) is None)
        )

    @abstractmethod
    async def close(self) -> None:
        """Release any resources held by the store."""
        ...
