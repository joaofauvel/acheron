"""In-memory implementations of the store ABCs."""

from __future__ import annotations

import copy
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from acheron.core.models import WorkerErrorEvent, WorkerStatus, sanitize_worker_error
from acheron.shell.job_store import JobQuery
from acheron.shell.stores.base import JobStore, StoreError, WorkerStore

if TYPE_CHECKING:
    from acheron.core.models import JsonValue, WorkerCapabilities, WorkerType
    from acheron.shell.job_store import TrackedJob
    from acheron.shell.registry import RegisteredWorker


class InMemoryWorkerStore(WorkerStore):
    """In-memory store of registered workers. State is lost on process restart."""

    _tombstone_ttl_seconds = 3600.0
    _max_history = 10

    def __init__(self) -> None:
        self._workers: dict[str, RegisteredWorker] = {}
        self._worker_history_tombstones: dict[str, tuple[float, tuple[WorkerErrorEvent, ...]]] = {}
        self._generations: dict[str, int] = {}

    def _purge_expired_tombstones(self) -> None:
        now = time.time()
        for worker_id, (expires_at, _) in tuple(self._worker_history_tombstones.items()):
            if expires_at <= now:
                del self._worker_history_tombstones[worker_id]

    def _history_for(self, worker_id: str) -> tuple[WorkerErrorEvent, ...]:
        worker = self._workers.get(worker_id)
        if worker is not None:
            return worker.error_history[-self._max_history :]
        tombstone = self._worker_history_tombstones.get(worker_id)
        return tombstone[1] if tombstone is not None else ()

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

        self._purge_expired_tombstones()
        history = self._history_for(worker_id)
        generation = self._generations.get(worker_id, 0) + 1
        self._generations[worker_id] = generation
        self._workers[worker_id] = RegisteredWorker(
            worker_id=worker_id,
            endpoint=endpoint,
            transport=transport,
            capabilities=capabilities,
            consecutive_failures=0,
            last_health_check=time.time(),
            metadata=metadata or {},
            booting_since=None,
            registration_generation=generation,
            error_history=history,
        )
        self._worker_history_tombstones.pop(worker_id, None)

    async def unregister(self, worker_id: str) -> None:
        """Remove a worker while retaining bounded history for re-registration."""
        self._purge_expired_tombstones()
        worker = self._workers.pop(worker_id, None)
        if worker is not None:
            self._worker_history_tombstones[worker_id] = (
                time.time() + self._tombstone_ttl_seconds,
                worker.error_history[-self._max_history :],
            )

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

    async def record_health_failure(
        self,
        worker_id: str,
        *,
        generation: int | None = None,
        error: str = "health check failed",
    ) -> bool:
        """Record a failed health check and retain its sanitized history."""
        self._purge_expired_tombstones()
        worker = self._workers.get(worker_id)
        if worker is None or (generation is not None and worker.registration_generation != generation):
            return False
        worker.consecutive_failures += 1
        worker.last_health_check = time.time()
        message = sanitize_worker_error(error)
        worker.last_error = message
        worker.status = WorkerStatus.OFFLINE
        event = WorkerErrorEvent(datetime.now(UTC), message, worker.consecutive_failures)
        worker.error_history = (*worker.error_history, event)[-self._max_history :]
        if worker.consecutive_failures >= self.max_failures:
            await self.unregister(worker_id)
            return True
        return False

    async def record_health_success(self, worker_id: str, *, generation: int | None = None) -> None:
        """Record a successful health check, resetting the failure counter and status."""
        self._purge_expired_tombstones()
        worker = self._workers.get(worker_id)
        if worker is not None and (generation is None or worker.registration_generation == generation):
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
        *,
        generation: int | None = None,
    ) -> None:
        """Update the worker's status and last_error."""
        self._purge_expired_tombstones()
        worker = self._workers.get(worker_id)
        if worker is not None and (generation is None or worker.registration_generation == generation):
            last_error = sanitize_worker_error(last_error) if last_error else None
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
        """Store or update a tracked job without losing an established archive marker."""
        existing = self._jobs.get(job.job_id)
        if existing is not None and existing.archived_at is not None and job.archived_at is None:
            job = copy.deepcopy(job)
            job.archived_at = existing.archived_at
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
