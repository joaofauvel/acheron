"""Integration tests for the Redis job store."""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
import redis

from acheron.core.models import (
    AudioRequest,
    EpubRequest,
    ExecutorStrategy,
    OutputFile,
    Plan,
    PlanResult,
    PlanStep,
    StepStatus,
    WorkerType,
)
from acheron.shell.job_store import TrackedJob
from acheron.shell.stores.redis import RedisJobStore


def _tracked(job_id: str = "job-1") -> TrackedJob:
    return TrackedJob(
        job_id=job_id,
        request=EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es"),
        strategy=ExecutorStrategy.BATCH_ASYNC,
    )


def _plan() -> Plan:
    return Plan(
        plan_id="plan-x",
        job_id="j-1",
        source_type="epub",
        source_language="en",
        target_language="es",
        executor_strategy=ExecutorStrategy.BATCH_ASYNC,
        steps=(
            PlanStep(
                step_id="extract",
                type=WorkerType.EXTRACTION,
                depends_on=(),
                status=StepStatus.COMPLETE,
                payload={"source_path": "/x"},
            ),
            PlanStep(
                step_id="synthesize",
                type=WorkerType.TTS,
                depends_on=("extract",),
                status=StepStatus.FAILED,
                payload={"target_language": "es"},
            ),
        ),
    )


def _result() -> PlanResult:
    return PlanResult(
        plan_id="plan-x",
        status="failed",
        completed_steps=2,
        total_steps=5,
        outputs=(
            OutputFile(
                path="/out/x.wav",
                filename="x.wav",
                size_bytes=42,
                checksum="abc",
                content_type="audio/wav",
            ),
        ),
        total_cost=0.5,
        total_duration_seconds=1.2,
        errors=("synthesize: GPU down",),
    )


@pytest_asyncio.fixture
async def store(redis_url: str) -> AsyncIterator[RedisJobStore]:
    s = RedisJobStore(redis_url)
    await s.connect()
    try:
        yield s
    finally:
        await s.close()


class TestPut:
    @pytest.mark.asyncio
    async def test_put_and_get(self, store: RedisJobStore) -> None:
        job = _tracked()
        await store.put(job)
        loaded = await store.get("job-1")
        assert loaded is not None
        assert loaded.job_id == "job-1"
        assert loaded.status == "pending"
        assert loaded.request.source_path == "/input/book.epub"
        assert loaded.request.source_language == "en"
        assert loaded.request.target_language == "es"
        assert loaded.strategy == ExecutorStrategy.BATCH_ASYNC

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, store: RedisJobStore) -> None:
        result = await store.get("nope")
        assert result is None

    @pytest.mark.asyncio
    async def test_put_overwrites(self, store: RedisJobStore) -> None:
        await store.put(_tracked("j-1"))
        job2 = _tracked("j-1")
        job2.status = "running"
        await store.put(job2)
        loaded = await store.get("j-1")
        assert loaded is not None
        assert loaded.status == "running"


class TestPlanRoundTrip:
    @pytest.mark.asyncio
    async def test_plan_with_steps_round_trips(self, store: RedisJobStore) -> None:
        job = _tracked()
        job.plan = _plan()
        await store.put(job)
        loaded = await store.get("job-1")
        assert loaded is not None
        assert loaded.plan is not None
        assert loaded.plan.plan_id == "plan-x"
        assert len(loaded.plan.steps) == 2
        assert loaded.plan.steps[0].type == WorkerType.EXTRACTION
        assert loaded.plan.steps[1].depends_on == ("extract",)
        assert loaded.plan.steps[1].status == StepStatus.FAILED
        assert loaded.plan.executor_strategy == ExecutorStrategy.BATCH_ASYNC

    @pytest.mark.asyncio
    async def test_result_round_trips(self, store: RedisJobStore) -> None:
        """Regression for C2: PlanResult must survive a Redis round-trip."""
        job = _tracked()
        job.result = _result()
        await store.put(job)
        loaded = await store.get("job-1")
        assert loaded is not None
        assert loaded.result is not None
        assert loaded.result.status == "failed"
        assert loaded.result.completed_steps == 2
        assert loaded.result.total_steps == 5
        assert loaded.result.total_cost == 0.5
        assert loaded.result.total_duration_seconds == 1.2
        assert loaded.result.errors == ("synthesize: GPU down",)
        assert len(loaded.result.outputs) == 1
        assert loaded.result.outputs[0].path == "/out/x.wav"
        assert loaded.result.outputs[0].checksum == "abc"


class TestAudioRequest:
    @pytest.mark.asyncio
    async def test_audio_request_with_asr_model_round_trips(self, store: RedisJobStore) -> None:
        """Regression for I10: AudioRequest.asr_model must round-trip."""
        job = TrackedJob(
            job_id="j-audio",
            request=AudioRequest(
                source_path="/in.mp3",
                source_language="en",
                target_language="es",
                asr_model="whisper-v3",
            ),
            strategy=ExecutorStrategy.SEQUENTIAL,
        )
        await store.put(job)
        loaded = await store.get("j-audio")
        assert loaded is not None
        assert isinstance(loaded.request, AudioRequest)
        assert loaded.request.asr_model == "whisper-v3"
        assert loaded.request.source_path == "/in.mp3"


class TestList:
    @pytest.mark.asyncio
    async def test_list_all(self, store: RedisJobStore) -> None:
        await store.put(_tracked("j-1"))
        await store.put(_tracked("j-2"))
        await store.put(_tracked("j-3"))
        jobs = await store.list_all()
        assert {j.job_id for j in jobs} == {"j-1", "j-2", "j-3"}

    @pytest.mark.asyncio
    async def test_list_empty(self, store: RedisJobStore) -> None:
        jobs = await store.list_all()
        assert jobs == ()


class TestFailFast:
    @pytest.mark.asyncio
    async def test_unreachable_redis_raises_on_connect(self) -> None:
        from redis.exceptions import ConnectionError as RedisConnectionError

        store = RedisJobStore("redis://localhost:1")
        with pytest.raises((RedisConnectionError, redis.RedisError)):
            await store.connect()
