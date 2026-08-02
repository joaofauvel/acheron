"""Safe preview and application of terminal-job retention cleanup."""

from __future__ import annotations

import asyncio
import heapq
import logging
from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from acheron.core.errors import CacheError, sanitise_exc_message
from acheron.core.models import PlanStatus
from acheron.shell.cache import InMemoryStepCache, PlanCache, StepCache, _delete_tree, _safe_path, _tree_size
from acheron.shell.input_store import InputPathError, InputStore
from acheron.shell.stores.base import StoreError

if TYPE_CHECKING:
    from acheron.shell.job_store import TrackedJob
    from acheron.shell.stores.base import JobStore


logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = frozenset({PlanStatus.COMPLETED, PlanStatus.FAILED, PlanStatus.PARTIAL})


@dataclass(frozen=True)
class RetentionPolicy:
    """Status-specific retention windows."""

    keep_successful: timedelta
    keep_failed: timedelta


@dataclass(frozen=True)
class CleanupCandidate:
    """One terminal job selected for cleanup."""

    job_id: str
    status: PlanStatus
    relative_paths: tuple[str, ...]
    reclaimable_bytes: int
    archived: bool = False


@dataclass(frozen=True)
class CleanupFailure:
    """A cleanup operation that could not safely complete."""

    job_id: str
    relative_paths: tuple[str, ...]
    message: str


@dataclass(frozen=True)
class CleanupReport:
    """Preview or application result."""

    apply: bool
    candidates: tuple[CleanupCandidate, ...]
    deleted_job_ids: tuple[str, ...]
    failures: tuple[CleanupFailure, ...]
    deleted_count: int
    deleted_bytes: int
    reclaimable_bytes: int


JobLockFactory = Callable[[str], asyncio.Lock]
InputLockFactory = Callable[[str], asyncio.Lock]
ActiveJobs = Callable[[], Collection[str]]


def _source_identity(data_dir: Path, source_path: str) -> str | None:
    try:
        return InputStore(data_dir, create=False).normalize_source_path(source_path)
    except InputPathError:
        return None


def _job_source_identity(data_dir: Path, job: TrackedJob) -> str | None:
    return _source_identity(data_dir, job.request.source_path)


def _eligible(job: TrackedJob, policy: RetentionPolicy, now: datetime) -> bool:
    if job.status not in _TERMINAL_STATUSES:
        return False
    retention = policy.keep_successful if job.status is PlanStatus.COMPLETED else policy.keep_failed
    return job.last_persisted_at <= now - retention


class RetentionService:
    """Coordinate filesystem-first cleanup with job and input lifecycle locks."""

    def __init__(  # noqa: PLR0913
        self,
        job_store: JobStore,
        plan_cache: PlanCache,
        step_cache: StepCache | InMemoryStepCache,
        *,
        data_dir: Path | str | None = None,
        job_lock: JobLockFactory | None = None,
        input_lock: InputLockFactory | None = None,
        active_jobs: ActiveJobs | None = None,
    ) -> None:
        self._job_store = job_store
        self._plan_cache = plan_cache
        self._step_cache = step_cache
        self._data_dir = Path(data_dir or plan_cache.data_dir).resolve()
        self._job_lock = job_lock or (lambda _job_id: asyncio.Lock())
        self._input_lock = input_lock or (lambda _identity: asyncio.Lock())
        self._active_jobs = active_jobs or (lambda: ())

    async def preview(self, policy: RetentionPolicy, *, now: datetime | None = None) -> CleanupReport:
        """Select eligible jobs and measure reclaimable data without mutation."""
        effective_now = self._normalise_now(now)
        jobs = await self._job_store.list_all()
        active = set(self._active_jobs())
        selected = tuple(
            heapq.nsmallest(
                1000,
                (job for job in jobs if job.job_id not in active and _eligible(job, policy, effective_now)),
                key=lambda j: j.job_id,
            )
        )
        retained_sources = {
            identity
            for job in jobs
            if job not in selected
            for identity in (_job_source_identity(self._data_dir, job),)
            if identity is not None
        }
        candidates = self._build_candidates(selected, retained_sources)
        return CleanupReport(
            apply=False,
            candidates=candidates,
            deleted_job_ids=(),
            failures=(),
            deleted_count=0,
            deleted_bytes=0,
            reclaimable_bytes=sum(candidate.reclaimable_bytes for candidate in candidates),
        )

    async def apply(self, policy: RetentionPolicy, *, now: datetime | None = None) -> CleanupReport:
        """Re-evaluate and safely delete eligible jobs, preserving records on failure."""
        effective_now = self._normalise_now(now)
        preview = await self.preview(policy, now=effective_now)
        all_jobs = await self._job_store.list_all()
        retained_inputs = self._retained_input_references(all_jobs, policy, effective_now)
        deleted: list[str] = []
        failures: list[CleanupFailure] = []
        deleted_bytes = 0
        for candidate in preview.candidates:
            async with self._job_lock(candidate.job_id):
                current = await self._job_store.get(candidate.job_id)
                if current is None:
                    continue
                if current.job_id in self._active_jobs() or not _eligible(current, policy, effective_now):
                    failures.append(
                        CleanupFailure(
                            candidate.job_id,
                            candidate.relative_paths,
                            "job is active or no longer eligible",
                        )
                    )
                    continue
                identity = _job_source_identity(self._data_dir, current)
                input_guard = self._input_lock(identity) if identity is not None else _NullAsyncLock()
                async with input_guard:
                    # Re-read references while holding the same lock used by input
                    # submission, so a concurrent job cannot race deletion.
                    current_jobs = await self._job_store.list_all()
                    concurrent_references = {
                        referenced
                        for job in current_jobs
                        if job.job_id != current.job_id
                        for referenced in (_job_source_identity(self._data_dir, job),)
                        if referenced is not None
                    }
                    retained = identity in retained_inputs or identity in concurrent_references
                    paths = tuple(path for path in candidate.relative_paths if path != identity or not retained)
                    try:
                        removed = await self._delete_paths(paths, current)
                    except OSError, CacheError, ValueError:
                        failures.append(
                            CleanupFailure(
                                candidate.job_id,
                                paths,
                                "filesystem cleanup failed; retry is safe",
                            )
                        )
                        continue
                    try:
                        await self._job_store.delete(current.job_id)
                    except StoreError as exc:
                        message = f"job record deletion failed; retry is safe ({sanitise_exc_message(exc)})"
                        logger.warning(
                            "Retention cleanup could not delete job record %s: %s",
                            current.job_id,
                            message,
                            exc_info=(type(exc), exc, exc.__traceback__),
                        )
                        failures.append(CleanupFailure(candidate.job_id, paths, message))
                        continue
                    deleted.append(current.job_id)
                    deleted_bytes += removed
        return CleanupReport(
            apply=True,
            candidates=preview.candidates,
            deleted_job_ids=tuple(deleted),
            failures=tuple(failures),
            deleted_count=len(deleted),
            deleted_bytes=deleted_bytes,
            reclaimable_bytes=preview.reclaimable_bytes,
        )

    async def cleanup(self, policy: RetentionPolicy, *, apply: bool, now: datetime | None = None) -> CleanupReport:
        """Run preview by default, applying changes only when explicitly requested."""
        return await self.apply(policy, now=now) if apply else await self.preview(policy, now=now)

    def _normalise_now(self, now: datetime | None) -> datetime:
        effective = datetime.now(UTC) if now is None else now
        if effective.tzinfo is None or effective.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        return effective.astimezone(UTC)

    def _build_candidates(
        self,
        jobs: tuple[TrackedJob, ...],
        retained_sources: set[str],
    ) -> tuple[CleanupCandidate, ...]:
        candidates: list[CleanupCandidate] = []
        seen_inputs: set[str] = set()
        for job in jobs:
            if not self._valid_job_id(job.job_id):
                continue
            paths: list[str] = []
            reclaimable = 0
            if self._exists(job.job_id):
                paths.append(job.job_id)
                reclaimable += self._size(job.job_id)
            if job.plan is not None and self._valid_plan_id(job.plan.plan_id) and self._exists(job.plan.plan_id):
                paths.append(job.plan.plan_id)
                reclaimable += self._size(job.plan.plan_id)
            identity = _job_source_identity(self._data_dir, job)
            if (
                identity is not None
                and identity not in retained_sources
                and identity not in seen_inputs
                and self._exists(identity)
            ):
                paths.append(identity)
                reclaimable += self._size(identity)
                seen_inputs.add(identity)
            candidates.append(
                CleanupCandidate(
                    job_id=job.job_id,
                    status=job.status,
                    relative_paths=tuple(paths),
                    reclaimable_bytes=reclaimable,
                    archived=job.archived_at is not None,
                )
            )
        return tuple(candidates)

    @staticmethod
    def _valid_component(value: str) -> bool:
        path = Path(value)
        return (
            bool(value)
            and "\\" not in value
            and value not in {".", ".."}
            and not path.is_absolute()
            and path.name == value
        )

    @classmethod
    def _valid_job_id(cls, value: str) -> bool:
        return cls._valid_component(value) and value.startswith("job-") and bool(value[4:])

    @classmethod
    def _valid_plan_id(cls, value: str) -> bool:
        return (
            cls._valid_component(value)
            and value.startswith("plan-")
            and bool(value[5:])
            and all(char in "0123456789abcdef" for char in value[5:])
        )

    def _exists(self, relative: str) -> bool:
        try:
            return (
                _safe_path(self._data_dir, Path(relative)).exists()
                or _safe_path(self._data_dir, Path(relative)).is_symlink()
            )
        except CacheError:
            return False

    def _size(self, relative: str) -> int:
        try:
            return _tree_size(_safe_path(self._data_dir, Path(relative)))
        except CacheError:
            return 0

    def _retained_input_references(
        self,
        jobs: tuple[TrackedJob, ...],
        policy: RetentionPolicy,
        now: datetime,
    ) -> set[str]:
        """Return input identities retained by jobs outside the cleanup policy."""
        return {
            identity
            for job in jobs
            if not _eligible(job, policy, now)
            for identity in (_job_source_identity(self._data_dir, job),)
            if identity is not None
        }

    async def _delete_paths(self, paths: tuple[str, ...], job: TrackedJob) -> int:
        removed = 0
        for relative in paths:
            if relative == job.job_id:
                removed += await self._step_cache.delete_job(job.job_id)
                if isinstance(self._step_cache, InMemoryStepCache):
                    removed += await asyncio.to_thread(_delete_tree, self._data_dir, Path(relative))
            elif job.plan is not None and relative == job.plan.plan_id:
                removed += await asyncio.to_thread(self._plan_cache.delete_plan, job.plan.plan_id)
            else:
                removed += await asyncio.to_thread(_delete_tree, self._data_dir, Path(relative))
        return removed


class _NullAsyncLock:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_args: object) -> None:
        return None


Retention = RetentionService

__all__ = [
    "CleanupCandidate",
    "CleanupFailure",
    "CleanupReport",
    "Retention",
    "RetentionPolicy",
    "RetentionService",
]
