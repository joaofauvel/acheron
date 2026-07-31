"""Parity tests for typed job-store lifecycle operations."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
import redis.asyncio as aioredis

from acheron.core.models import EpubRequest, ExecutorStrategy, PlanStatus
from acheron.shell.job_store import JobQuery, TrackedJob
from acheron.shell.stores.base import JobStore
from acheron.shell.stores.memory import InMemoryJobStore
from acheron.shell.stores.redis import RedisJobStore


def _tracked(job_id: str, *, status: PlanStatus, created_at: datetime) -> TrackedJob:
    return TrackedJob(
        job_id=job_id,
        request=EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es"),
        strategy=ExecutorStrategy.STREAMING,
        status=status,
        created_at=created_at,
        last_persisted_at=created_at,
    )


@pytest_asyncio.fixture(params=["memory", "redis"])
async def job_store(request: pytest.FixtureRequest, redis_url: str) -> AsyncIterator[JobStore]:
    if request.param == "memory":
        yield InMemoryJobStore()
        return
    store = RedisJobStore(redis_url)
    await store.connect()
    try:
        yield store
    finally:
        await store.close()


async def _set_persisted_at(store: JobStore, redis_url: str, job_id: str, timestamp: datetime) -> None:
    job = await store.get(job_id)
    assert job is not None
    job.last_persisted_at = timestamp
    if isinstance(store, InMemoryJobStore):
        store._jobs[job_id].last_persisted_at = timestamp  # noqa: SLF001
        return
    from acheron.shell.stores.redis import _JOB_KEY, _serialize_job

    redis = aioredis.Redis.from_url(redis_url, decode_responses=True)
    try:
        await redis.set(_JOB_KEY.format(job_id=job_id), _serialize_job(job))
    finally:
        await redis.aclose()


@pytest.mark.asyncio
async def test_memory_and_redis_filter_archive_and_delete_with_same_behavior(
    job_store: JobStore,
    redis_url: str,
) -> None:
    now = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
    jobs = (
        _tracked("job-completed", status=PlanStatus.COMPLETED, created_at=now - timedelta(hours=3)),
        _tracked("job-failed", status=PlanStatus.FAILED, created_at=now - timedelta(hours=2)),
        _tracked("job-stuck", status=PlanStatus.RUNNING, created_at=now - timedelta(hours=1)),
        _tracked("job-recent", status=PlanStatus.RUNNING, created_at=now - timedelta(minutes=5)),
    )
    for job in jobs:
        await job_store.put(job)
    await _set_persisted_at(job_store, redis_url, "job-stuck", now - timedelta(hours=1))
    await _set_persisted_at(job_store, redis_url, "job-recent", now - timedelta(minutes=5))

    archived_at = datetime(2026, 7, 30, 13, 0, tzinfo=UTC)
    archived = await job_store.archive("job-completed", archived_at=archived_at)
    assert archived.archived_at == archived_at
    archived_again = await job_store.archive("job-completed", archived_at=archived_at + timedelta(hours=1))
    assert archived_again.archived_at == archived_at

    assert [
        job.job_id
        for job in await job_store.list(JobQuery(status=PlanStatus.RUNNING, older_than_seconds=1800), now=now)
    ] == ["job-stuck"]
    assert [job.job_id for job in await job_store.list(JobQuery())] == ["job-failed", "job-recent", "job-stuck"]
    assert [job.job_id for job in await job_store.list(JobQuery(include_archived=True))] == [
        "job-completed",
        "job-failed",
        "job-recent",
        "job-stuck",
    ]

    removed = await job_store.delete("job-failed")
    assert removed is not None
    assert removed.job_id == "job-failed"
    assert await job_store.get("job-failed") is None
    assert await job_store.delete("missing") is None
