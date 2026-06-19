"""Tests for the job store."""

import pytest

from acheron.core.models import EpubRequest, ExecutorStrategy
from acheron.shell.job_store import TrackedJob
from acheron.shell.stores.memory import InMemoryJobStore


def _tracked(job_id: str = "job-1") -> TrackedJob:
    return TrackedJob(
        job_id=job_id,
        request=EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es"),
        strategy=ExecutorStrategy.BATCH_ASYNC,
    )


class TestJobStore:
    @pytest.mark.asyncio
    async def test_put_and_get(self) -> None:
        store = InMemoryJobStore()
        job = _tracked()
        await store.put(job)
        assert await store.get("job-1") is job

    @pytest.mark.asyncio
    async def test_get_nonexistent(self) -> None:
        store = InMemoryJobStore()
        assert await store.get("nope") is None

    @pytest.mark.asyncio
    async def test_list_all(self) -> None:
        store = InMemoryJobStore()
        await store.put(_tracked("j-1"))
        await store.put(_tracked("j-2"))
        await store.put(_tracked("j-3"))
        assert len(await store.list_all()) == 3

    @pytest.mark.asyncio
    async def test_list_empty(self) -> None:
        store = InMemoryJobStore()
        assert await store.list_all() == ()

    @pytest.mark.asyncio
    async def test_put_overwrites(self) -> None:
        store = InMemoryJobStore()
        job1 = _tracked("j-1")
        job2 = _tracked("j-1")
        await store.put(job1)
        await store.put(job2)
        assert await store.get("j-1") is job2

    @pytest.mark.asyncio
    async def test_status_update(self) -> None:
        store = InMemoryJobStore()
        job = _tracked()
        await store.put(job)
        job.status = "running"
        await store.put(job)
        stored = await store.get("job-1")
        assert stored is not None
        assert stored.status == "running"
