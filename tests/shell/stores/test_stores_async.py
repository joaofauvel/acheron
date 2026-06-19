"""Async contract tests for the store ABCs and InMemory implementations.

Verifies that store methods are coroutine functions and that ``await`` on
each method produces the expected side effect. Locks in the Layer 9b-i
async migration contract.
"""

from __future__ import annotations

import inspect

import pytest

from acheron.core.models import EpubRequest, ExecutorStrategy, WorkerCapabilities, WorkerType
from acheron.shell.job_store import TrackedJob
from acheron.shell.stores.base import JobStore, WorkerStore
from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore


def _tts_caps() -> WorkerCapabilities:
    return WorkerCapabilities(
        worker_type=WorkerType.TTS,
        supported_languages_in=frozenset({"en"}),
        supported_languages_out=frozenset({"es"}),
        supported_formats_in=frozenset({"text"}),
        supported_formats_out=frozenset({"wav"}),
        max_payload_bytes=None,
        batch_capable=True,
        model_source=None,
    )


def _tracked(job_id: str = "job-1") -> TrackedJob:
    return TrackedJob(
        job_id=job_id,
        request=EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es"),
        strategy=ExecutorStrategy.BATCH_ASYNC,
    )


class TestWorkerStoreABCCoroutineContract:
    @pytest.mark.parametrize(
        "method_name",
        [
            "register",
            "unregister",
            "get",
            "list_all",
            "find_by_type",
            "find_by_language",
            "record_health_failure",
            "record_health_success",
            "close",
        ],
    )
    def test_method_is_coroutine_function(self, method_name: str) -> None:
        method = getattr(WorkerStore, method_name)
        assert inspect.iscoroutinefunction(method), f"WorkerStore.{method_name} must be async def"


class TestJobStoreABCCoroutineContract:
    @pytest.mark.parametrize(
        "method_name",
        ["put", "get", "list_all", "close"],
    )
    def test_method_is_coroutine_function(self, method_name: str) -> None:
        method = getattr(JobStore, method_name)
        assert inspect.iscoroutinefunction(method), f"JobStore.{method_name} must be async def"


class TestInMemoryWorkerStoreAsyncBehavior:
    @pytest.mark.asyncio
    async def test_register_and_get_round_trip(self) -> None:
        store = InMemoryWorkerStore()
        await store.register("w-1", "http://localhost:8001", "http", _tts_caps())
        worker = await store.get("w-1")
        assert worker is not None
        assert worker.worker_id == "w-1"
        assert worker.endpoint == "http://localhost:8001"

    @pytest.mark.asyncio
    async def test_unregister_removes_worker(self) -> None:
        store = InMemoryWorkerStore()
        await store.register("w-1", "http://a", "http", _tts_caps())
        await store.unregister("w-1")
        assert await store.get("w-1") is None

    @pytest.mark.asyncio
    async def test_list_all_returns_all_workers(self) -> None:
        store = InMemoryWorkerStore()
        await store.register("w-1", "http://a", "http", _tts_caps())
        await store.register("w-2", "http://b", "http", _tts_caps())
        workers = await store.list_all()
        assert {w.worker_id for w in workers} == {"w-1", "w-2"}

    @pytest.mark.asyncio
    async def test_record_health_failure_increments(self) -> None:
        store = InMemoryWorkerStore()
        await store.register("w-1", "http://a", "http", _tts_caps())
        removed = await store.record_health_failure("w-1")
        assert removed is False
        worker = await store.get("w-1")
        assert worker is not None
        assert worker.consecutive_failures == 1

    @pytest.mark.asyncio
    async def test_close_completes(self) -> None:
        store = InMemoryWorkerStore()
        await store.close()


class TestInMemoryJobStoreAsyncBehavior:
    @pytest.mark.asyncio
    async def test_put_and_get_round_trip(self) -> None:
        store = InMemoryJobStore()
        job = _tracked()
        await store.put(job)
        loaded = await store.get("job-1")
        assert loaded is not None
        assert loaded.job_id == "job-1"

    @pytest.mark.asyncio
    async def test_list_all_returns_all_jobs(self) -> None:
        store = InMemoryJobStore()
        await store.put(_tracked("j-1"))
        await store.put(_tracked("j-2"))
        jobs = await store.list_all()
        assert {j.job_id for j in jobs} == {"j-1", "j-2"}

    @pytest.mark.asyncio
    async def test_close_completes(self) -> None:
        store = InMemoryJobStore()
        await store.close()
