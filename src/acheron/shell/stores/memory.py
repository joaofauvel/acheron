"""In-memory implementations of the store ABCs."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from acheron.shell.stores.base import JobStore, WorkerStore

if TYPE_CHECKING:
    from acheron.core.models import JsonValue, WorkerCapabilities, WorkerType
    from acheron.shell.job_store import TrackedJob
    from acheron.shell.registry import RegisteredWorker


class InMemoryWorkerStore(WorkerStore):
    """In-memory store of registered workers. State is lost on process restart."""

    def __init__(self) -> None:
        self._workers: dict[str, RegisteredWorker] = {}

    async def register(
        self,
        worker_id: str,
        endpoint: str,
        transport: str,
        capabilities: WorkerCapabilities,
        metadata: dict[str, JsonValue] | None = None,
    ) -> None:
        """Register a new worker or re-register an existing one."""
        from acheron.shell.registry import RegisteredWorker  # noqa: PLC0415

        self._workers[worker_id] = RegisteredWorker(
            worker_id=worker_id,
            endpoint=endpoint,
            transport=transport,
            capabilities=capabilities,
            consecutive_failures=0,
            last_health_check=time.time(),
            metadata=metadata or {},
        )

    async def unregister(self, worker_id: str) -> None:
        """Remove a worker from the store."""
        self._workers.pop(worker_id, None)

    async def get(self, worker_id: str) -> RegisteredWorker | None:
        """Look up a worker by ID."""
        return self._workers.get(worker_id)

    async def list_all(self) -> tuple[RegisteredWorker, ...]:
        """Return all registered workers."""
        return tuple(self._workers.values())

    async def find_by_type(self, worker_type: WorkerType) -> tuple[RegisteredWorker, ...]:
        """Find workers matching a given WorkerType."""
        return tuple(w for w in await self.list_all() if w.capabilities.worker_type == worker_type)

    async def find_by_language(self, src: str, dst: str) -> tuple[RegisteredWorker, ...]:
        """Find workers supporting a source→target language pair."""
        workers = await self.list_all()
        return tuple(
            w
            for w in workers
            if src in w.capabilities.supported_languages_in and dst in w.capabilities.supported_languages_out
        )

    async def record_health_failure(self, worker_id: str) -> bool:
        """Record a failed health check. Returns True if the worker was removed."""
        worker = self._workers.get(worker_id)
        if worker is None:
            return False
        worker.consecutive_failures += 1
        worker.last_health_check = time.time()
        if worker.consecutive_failures >= self.max_failures:
            await self.unregister(worker_id)
            return True
        return False

    async def record_health_success(self, worker_id: str) -> None:
        """Record a successful health check, resetting the failure counter."""
        worker = self._workers.get(worker_id)
        if worker is not None:
            worker.consecutive_failures = 0
            worker.last_health_check = time.time()

    async def close(self) -> None:
        """No-op for the in-memory store."""
        return


class InMemoryJobStore(JobStore):
    """In-memory store of tracked jobs. State is lost on process restart."""

    def __init__(self) -> None:
        self._jobs: dict[str, TrackedJob] = {}

    async def put(self, job: TrackedJob) -> None:
        """Store or update a tracked job."""
        self._jobs[job.job_id] = job

    async def get(self, job_id: str) -> TrackedJob | None:
        """Retrieve a tracked job by ID."""
        return self._jobs.get(job_id)

    async def list_all(self) -> tuple[TrackedJob, ...]:
        """Return all tracked jobs."""
        return tuple(self._jobs.values())

    async def close(self) -> None:
        """No-op for the in-memory store."""
        return
