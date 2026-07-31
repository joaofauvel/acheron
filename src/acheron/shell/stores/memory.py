"""In-memory implementations of the store ABCs."""

from __future__ import annotations

import copy
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from acheron.core.models import WorkerStatus
from acheron.shell.job_store import JobQuery
from acheron.shell.stores.base import JobStore, StoreError, WorkerStore

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
            booting_since=None,
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
        """Record a successful health check, resetting the failure counter and status."""
        worker = self._workers.get(worker_id)
        if worker is not None:
            worker.consecutive_failures = 0
            worker.last_health_check = time.time()
            worker.status = WorkerStatus.HEALTHY
            worker.last_error = None
            worker.booting_since = None

    async def set_worker_status(
        self,
        worker_id: str,
        status: WorkerStatus,
        last_error: str | None,
    ) -> None:
        """Update the worker's status and last_error."""
        worker = self._workers.get(worker_id)
        if worker is not None:
            if status == WorkerStatus.BOOTING:
                if worker.status != WorkerStatus.BOOTING or worker.booting_since is None:
                    worker.booting_since = time.time()
            else:
                worker.booting_since = None
            worker.status = status
            worker.last_error = last_error

    async def close(self) -> None:
        """No-op for the in-memory store."""
        return


class InMemoryJobStore(JobStore):
    """In-memory store of tracked jobs. State is lost on process restart."""

    def __init__(self) -> None:
        self._jobs: dict[str, TrackedJob] = {}

    async def put(self, job: TrackedJob) -> None:
        """Store or update a tracked job."""
        job.last_persisted_at = datetime.now(UTC)
        self._jobs[job.job_id] = copy.deepcopy(job)

    async def get(self, job_id: str) -> TrackedJob | None:
        """Retrieve a tracked job by ID."""
        job = self._jobs.get(job_id)
        return copy.deepcopy(job) if job is not None else None

    async def list_all(self) -> tuple[TrackedJob, ...]:
        """Return all tracked jobs."""
        return tuple(copy.deepcopy(job) for job in self._jobs.values())

    async def list(self, query: JobQuery = JobQuery(), *, now: datetime | None = None) -> tuple[TrackedJob, ...]:  # noqa: B008
        """Return tracked jobs matching a typed query in deterministic order."""
        jobs = (copy.deepcopy(self._jobs[job_id]) for job_id in sorted(self._jobs))
        return self._filter_jobs(jobs, query, now=now)

    async def archive(self, job_id: str, *, archived_at: datetime | None = None) -> TrackedJob:
        """Mark a job archived and return the persisted record."""
        job = await self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job.archived_at is None:
            timestamp = archived_at if archived_at is not None else datetime.now(UTC)
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                msg = "archived_at must be timezone-aware"
                raise ValueError(msg)
            job.archived_at = timestamp.astimezone(UTC)
        await self.put(job)
        stored = await self.get(job_id)
        if stored is None:
            msg = f"Job {job_id} disappeared after archive"
            raise StoreError(msg)
        return stored

    async def delete(self, job_id: str) -> TrackedJob | None:
        """Delete a job and return its removed record, if present."""
        job = self._jobs.pop(job_id, None)
        return copy.deepcopy(job) if job is not None else None

    async def close(self) -> None:
        """No-op for the in-memory store."""
        return
