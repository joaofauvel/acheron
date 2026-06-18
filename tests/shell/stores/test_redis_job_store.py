"""Integration tests for the Redis job store."""

import pytest
import redis

from acheron.core.models import EpubRequest, ExecutorStrategy
from acheron.shell.job_store import TrackedJob
from acheron.shell.stores.redis import RedisJobStore


def _tracked(job_id: str = "job-1") -> TrackedJob:
    return TrackedJob(
        job_id=job_id,
        request=EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es"),
        strategy=ExecutorStrategy.BATCH_ASYNC,
    )


@pytest.fixture
def store(redis_url: str) -> RedisJobStore:
    return RedisJobStore(redis_url)


class TestPut:
    def test_put_and_get(self, store: RedisJobStore) -> None:
        job = _tracked()
        store.put(job)
        loaded = store.get("job-1")
        assert loaded is not None
        assert loaded.job_id == "job-1"
        assert loaded.status == "pending"
        assert loaded.request.source_path == "/input/book.epub"
        assert loaded.request.source_language == "en"
        assert loaded.request.target_language == "es"
        assert loaded.strategy == ExecutorStrategy.BATCH_ASYNC

    def test_get_nonexistent(self, store: RedisJobStore) -> None:
        result = store.get("nope")
        assert result is None

    def test_put_overwrites(self, store: RedisJobStore) -> None:
        store.put(_tracked("j-1"))
        job2 = _tracked("j-1")
        job2.status = "running"
        store.put(job2)
        loaded = store.get("j-1")
        assert loaded is not None
        assert loaded.status == "running"


class TestList:
    def test_list_all(self, store: RedisJobStore) -> None:
        store.put(_tracked("j-1"))
        store.put(_tracked("j-2"))
        store.put(_tracked("j-3"))
        jobs = store.list_all()
        assert {j.job_id for j in jobs} == {"j-1", "j-2", "j-3"}

    def test_list_empty(self, store: RedisJobStore) -> None:
        jobs = store.list_all()
        assert jobs == ()


class TestFailFast:
    def test_unreachable_redis_raises_on_init(self) -> None:
        with pytest.raises(redis.RedisError):
            RedisJobStore("redis://localhost:1")
