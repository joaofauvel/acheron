"""Tests for the job store."""

from acheron.core.models import EpubRequest, ExecutorStrategy
from acheron.shell.job_store import JobStore, TrackedJob


def _tracked(job_id: str = "job-1") -> TrackedJob:
    return TrackedJob(
        job_id=job_id,
        request=EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es"),
        strategy=ExecutorStrategy.BATCH_ASYNC,
    )


class TestJobStore:
    def test_put_and_get(self) -> None:
        store = JobStore()
        job = _tracked()
        store.put(job)
        assert store.get("job-1") is job

    def test_get_nonexistent(self) -> None:
        store = JobStore()
        assert store.get("nope") is None

    def test_list_all(self) -> None:
        store = JobStore()
        store.put(_tracked("j-1"))
        store.put(_tracked("j-2"))
        store.put(_tracked("j-3"))
        assert len(store.list_all()) == 3

    def test_list_empty(self) -> None:
        store = JobStore()
        assert store.list_all() == ()

    def test_put_overwrites(self) -> None:
        store = JobStore()
        job1 = _tracked("j-1")
        job2 = _tracked("j-1")
        store.put(job1)
        store.put(job2)
        assert store.get("j-1") is job2

    def test_status_update(self) -> None:
        store = JobStore()
        job = _tracked()
        store.put(job)
        job.status = "running"
        store.put(job)
        stored = store.get("job-1")
        assert stored is not None
        assert stored.status == "running"
