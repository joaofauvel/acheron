"""Tests for the job store."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from acheron.core.models import (
    CostBasis,
    CostBreakdown,
    CostEstimate,
    EpubRequest,
    ExecutorStrategy,
    PlanResult,
    PlanStatus,
    VoiceRange,
    WorkerType,
)
from acheron.shell.job_store import TrackedJob
from acheron.shell.stores.memory import InMemoryJobStore


def _tracked(job_id: str = "job-1") -> TrackedJob:
    return TrackedJob(
        job_id=job_id,
        request=EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es"),
        strategy=ExecutorStrategy.STREAMING,
    )


class TestJobStore:
    @pytest.mark.asyncio
    async def test_voice_selection_round_trips(self) -> None:
        store = InMemoryJobStore()
        job = TrackedJob(
            job_id="voice-job",
            request=EpubRequest(
                source_path="/input/book.epub",
                source_language="en",
                target_language="es",
                voice="Vivian",
                voice_map=(VoiceRange(1, 3, "Vivian"),),
            ),
            strategy=ExecutorStrategy.STREAMING,
        )
        await store.put(job)
        stored = await store.get("voice-job")
        assert stored is not None
        assert stored.request == job.request

    @pytest.mark.asyncio
    async def test_put_and_get(self) -> None:
        store = InMemoryJobStore()
        job = _tracked()
        await store.put(job)
        stored = await store.get("job-1")
        assert stored is not None
        assert stored.job_id == job.job_id

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
        stored = await store.get("j-1")
        assert stored is not None
        assert stored.job_id == job2.job_id

    @pytest.mark.asyncio
    async def test_cost_breakdown_round_trips_for_failed_result(self) -> None:
        store = InMemoryJobStore()
        job = _tracked()
        estimate = CostEstimate(cost=None, basis=CostBasis.UNKNOWN, gpu_type="L4")
        job.result = PlanResult(
            plan_id="plan-1",
            status=PlanStatus.FAILED,
            completed_steps=0,
            total_steps=1,
            outputs=(),
            total_cost=0.0,
            total_duration_seconds=1.0,
            cost_breakdown=(CostBreakdown("synthesize", WorkerType.TTS, "tts-1", 1.0, estimate),),
        )
        await store.put(job)
        stored = await store.get("job-1")
        assert stored is not None
        assert stored.result is not None
        assert stored.result.cost_breakdown[0].estimate == estimate

    @pytest.mark.asyncio
    async def test_status_update(self) -> None:
        store = InMemoryJobStore()
        job = _tracked()
        await store.put(job)
        job.status = PlanStatus.RUNNING
        await store.put(job)
        stored = await store.get("job-1")
        assert stored is not None
        assert stored.status == PlanStatus.RUNNING

    def test_lifecycle_timestamps_reject_naive_values(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            TrackedJob(
                job_id="job-naive",
                request=_tracked().request,
                strategy=ExecutorStrategy.STREAMING,
                created_at=datetime.fromisoformat("2026-07-29T00:00:00"),
            )

    def test_lifecycle_timestamps_normalize_to_utc(self) -> None:
        offset = timezone(timedelta(hours=2))
        job = TrackedJob(
            job_id="job-offset",
            request=_tracked().request,
            strategy=ExecutorStrategy.STREAMING,
            created_at=datetime(2026, 7, 29, 14, tzinfo=offset),
            last_persisted_at=datetime(2026, 7, 29, 14, tzinfo=offset),
        )

        assert job.created_at == datetime(2026, 7, 29, 12, tzinfo=UTC)
        assert job.last_persisted_at == datetime(2026, 7, 29, 12, tzinfo=UTC)
