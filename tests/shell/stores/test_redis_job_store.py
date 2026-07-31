"""Integration tests for the Redis job store."""

import json
import math
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Self, cast

import pytest
import pytest_asyncio
import redis
import redis.asyncio as aioredis
from redis.exceptions import ConnectionError as RedisConnectionError

from acheron.core.models import (
    AudioRequest,
    CostBasis,
    CostBreakdown,
    CostEstimate,
    EpubRequest,
    ExecutorStrategy,
    OutputFile,
    Plan,
    PlanResult,
    PlanStatus,
    PlanStep,
    StepError,
    StepStatus,
    WorkerType,
)
from acheron.shell.job_store import JobProgressState, TrackedJob
from acheron.shell.stores.base import StoreError
from acheron.shell.stores.redis import RedisJobStore, _RedisAwaitable


def _tracked(job_id: str = "job-1") -> TrackedJob:
    return TrackedJob(
        job_id=job_id,
        request=EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es"),
        strategy=ExecutorStrategy.STREAMING,
    )


def _plan() -> Plan:
    return Plan(
        plan_id="plan-x",
        job_id="j-1",
        source_type="epub",
        source_language="en",
        target_language="es",
        executor_strategy=ExecutorStrategy.STREAMING,
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
        status=PlanStatus.FAILED,
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
        errors=(
            StepError(
                step_id="synthesize",
                worker_type=WorkerType.TTS,
                worker_id="tts-1",
                message="GPU down",
                timestamp=datetime(2026, 7, 29, 12, 0, tzinfo=UTC),
            ),
        ),
        total_cost_basis=CostBasis.UNKNOWN,
        cost_breakdown=(
            CostBreakdown(
                step_id="synthesize",
                worker_type=WorkerType.TTS,
                worker_id="tts-1",
                gpu_seconds=1800.0,
                estimate=CostEstimate(
                    cost=0.34,
                    basis=CostBasis.MEASURED,
                    rate_per_hour=0.69,
                    gpu_type="L4",
                    secure_cloud=False,
                    queried_at=datetime(2026, 7, 30, tzinfo=UTC),
                    cache_age_seconds=0.0,
                ),
            ),
        ),
    )


@pytest_asyncio.fixture
async def store(redis_url: str) -> AsyncIterator[RedisJobStore]:
    s = RedisJobStore(redis_url)
    await s.connect()
    try:
        yield s
    finally:
        await s.close()


async def _set_breakdown_fields(
    store: RedisJobStore,
    redis_url: str,
    *,
    item_fields: dict[str, object] | None = None,
    estimate_fields: dict[str, object] | None = None,
) -> None:
    from acheron.shell.stores.redis import _JOB_KEY

    job = _tracked("j-corrupt")
    job.result = _result()
    await store.put(job)
    r = aioredis.Redis.from_url(redis_url, decode_responses=True)
    try:
        blob = cast("str", await r.get(_JOB_KEY.format(job_id="j-corrupt")))
        payload = cast("dict[str, object]", json.loads(blob))
        result = cast("dict[str, object]", payload["result"])
        breakdown = cast("list[dict[str, object]]", result["cost_breakdown"])
        item = breakdown[0]
        if item_fields is not None:
            item.update(item_fields)
        if estimate_fields is not None:
            estimate = cast("dict[str, object]", item["estimate"])
            estimate.update(estimate_fields)
        await r.set(_JOB_KEY.format(job_id="j-corrupt"), json.dumps(payload, allow_nan=True))
    finally:
        await r.aclose()


class TestDelete:
    @pytest.mark.asyncio
    async def test_delete_returns_record_at_atomic_removal_boundary(
        self,
        store: RedisJobStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from acheron.shell.stores.redis import _DELETE_JOB_SCRIPT, _JOB_KEY

        job = _tracked("job-race")
        job.label = "before-update"
        await store.put(job)
        concurrent_update = _tracked("job-race")
        concurrent_update.label = "concurrent-update"

        original_eval = store._redis.eval  # noqa: SLF001

        async def eval_with_concurrent_update(script: str, numkeys: int, *keys_and_args: str) -> object:
            if script == _DELETE_JOB_SCRIPT:
                assert keys_and_args[0] == _JOB_KEY.format(job_id="job-race")
                await store.put(concurrent_update)
            return await original_eval(script, numkeys, *keys_and_args)

        monkeypatch.setattr(store._redis, "eval", eval_with_concurrent_update)  # noqa: SLF001

        removed = await store.delete("job-race")

        assert removed == concurrent_update


class TestPut:
    @pytest.mark.asyncio
    async def test_put_and_get(self, store: RedisJobStore) -> None:
        job = _tracked()
        await store.put(job)
        loaded = await store.get("job-1")
        assert loaded is not None
        assert loaded.job_id == "job-1"
        assert loaded.status == PlanStatus.PENDING
        assert loaded.request.source_path == "/input/book.epub"
        assert loaded.request.source_language == "en"
        assert loaded.request.target_language == "es"
        assert loaded.strategy == ExecutorStrategy.STREAMING

    @pytest.mark.asyncio
    async def test_phase_4c_fields_round_trip(self, store: RedisJobStore) -> None:
        created = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
        job = TrackedJob(
            job_id="job-phase-4c",
            request=AudioRequest("/data/book.wav", "en", "es", "whisper-v3"),
            strategy=ExecutorStrategy.STREAMING,
            label="atlas-ch1",
            retries_from="job-old",
            created_at=created,
            last_persisted_at=created,
            progress=JobProgressState(
                completed_steps=2,
                total_steps=5,
                current_step_id="step-3",
                current_worker_type=WorkerType.TTS,
                current_worker_id="tts-1",
            ),
            result=_result(),
            status=PlanStatus.FAILED,
        )

        await store.put(job)
        loaded = await store.get("job-phase-4c")

        assert loaded is not None
        assert loaded.label == "atlas-ch1"
        assert loaded.retries_from == "job-old"
        assert loaded.created_at == created
        assert loaded.progress.current_worker_id == "tts-1"
        assert loaded.result is not None
        assert loaded.result.errors[0].worker_id == "tts-1"

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, store: RedisJobStore) -> None:
        result = await store.get("nope")
        assert result is None

    @pytest.mark.asyncio
    async def test_corrupt_job_blob_raises_cache_corrupted(self, store: RedisJobStore, redis_url: str) -> None:
        """A corrupt JSON job blob must raise CacheCorruptedError, not raw JSONDecodeError."""
        from acheron.core.errors import CacheCorruptedError
        from acheron.shell.stores.redis import _JOB_KEY

        r = aioredis.Redis.from_url(redis_url)
        await r.set(_JOB_KEY.format(job_id="j-corrupt"), "{ not valid json")
        await r.aclose()
        with pytest.raises(CacheCorruptedError, match="Job blob is not valid JSON"):
            await store.get("j-corrupt")

    @pytest.mark.asyncio
    async def test_put_overwrites(self, store: RedisJobStore) -> None:
        await store.put(_tracked("j-1"))
        job2 = _tracked("j-1")
        job2.status = PlanStatus.RUNNING
        await store.put(job2)
        loaded = await store.get("j-1")
        assert loaded is not None
        assert loaded.status == PlanStatus.RUNNING


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
        assert loaded.plan.executor_strategy == ExecutorStrategy.STREAMING

    @pytest.mark.asyncio
    async def test_result_round_trips(self, store: RedisJobStore) -> None:
        """Regression for C2: PlanResult must survive a Redis round-trip."""
        job = _tracked()
        job.result = _result()
        await store.put(job)
        loaded = await store.get("job-1")
        assert loaded is not None
        assert loaded.result is not None
        assert loaded.result.status == PlanStatus.FAILED
        assert loaded.result.completed_steps == 2
        assert loaded.result.total_steps == 5
        assert loaded.result.total_cost == 0.5
        assert loaded.result.total_duration_seconds == 1.2
        assert loaded.result.errors[0].message == "GPU down"
        assert loaded.result.errors[0].worker_id == "tts-1"
        assert loaded.result.total_cost_basis == CostBasis.UNKNOWN
        assert len(loaded.result.cost_breakdown) == 1
        item = loaded.result.cost_breakdown[0]
        assert item.worker_id == "tts-1"
        assert item.estimate == _result().cost_breakdown[0].estimate
        assert len(loaded.result.outputs) == 1
        assert loaded.result.outputs[0].path == "/out/x.wav"
        assert loaded.result.outputs[0].checksum == "abc"

    @pytest.mark.parametrize("queried_at", ["", 0, False, "not-a-timestamp"])
    @pytest.mark.asyncio
    async def test_malformed_queried_at_raises_cache_corrupted(
        self,
        store: RedisJobStore,
        redis_url: str,
        queried_at: object,
    ) -> None:
        from acheron.core.errors import CacheCorruptedError

        await _set_breakdown_fields(store, redis_url, estimate_fields={"queried_at": queried_at})
        with pytest.raises(CacheCorruptedError):
            await store.get("j-corrupt")

    @pytest.mark.asyncio
    async def test_null_queried_at_remains_valid(self, store: RedisJobStore, redis_url: str) -> None:
        await _set_breakdown_fields(store, redis_url, estimate_fields={"queried_at": None})
        loaded = await store.get("j-corrupt")
        assert loaded is not None
        assert loaded.result is not None
        assert loaded.result.cost_breakdown[0].estimate.queried_at is None

    @pytest.mark.asyncio
    async def test_malformed_cost_basis_raises_cache_corrupted(self, store: RedisJobStore, redis_url: str) -> None:
        from acheron.core.errors import CacheCorruptedError

        await _set_breakdown_fields(store, redis_url, estimate_fields={"basis": "invalid"})
        with pytest.raises(CacheCorruptedError):
            await store.get("j-corrupt")

    @pytest.mark.asyncio
    async def test_negative_cache_age_raises_cache_corrupted(self, store: RedisJobStore, redis_url: str) -> None:
        from acheron.core.errors import CacheCorruptedError

        await _set_breakdown_fields(store, redis_url, estimate_fields={"cache_age_seconds": -1.0})
        with pytest.raises(CacheCorruptedError):
            await store.get("j-corrupt")

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("gpu_seconds", math.nan),
            ("cost", math.inf),
            ("rate_per_hour", -math.inf),
            ("cache_age_seconds", math.nan),
        ],
    )
    @pytest.mark.asyncio
    async def test_non_finite_numeric_fields_raise_cache_corrupted(
        self,
        store: RedisJobStore,
        redis_url: str,
        field: str,
        value: float,
    ) -> None:
        from acheron.core.errors import CacheCorruptedError

        fields: dict[str, object] = {field: value}
        if field == "gpu_seconds":
            await _set_breakdown_fields(store, redis_url, item_fields=fields)
        else:
            await _set_breakdown_fields(store, redis_url, estimate_fields=fields)
        with pytest.raises(CacheCorruptedError):
            await store.get("j-corrupt")

    @pytest.mark.asyncio
    async def test_result_with_metadata_round_trips(self, store: RedisJobStore) -> None:
        """CORR-035: OutputFile.metadata must survive a Redis round-trip."""
        job = _tracked()
        job.result = PlanResult(
            plan_id="plan-x",
            status=PlanStatus.FAILED,
            completed_steps=2,
            total_steps=5,
            outputs=(
                OutputFile(
                    path="/out/x.wav",
                    filename="x.wav",
                    size_bytes=42,
                    checksum="abc",
                    content_type="audio/wav",
                    metadata={"sequence_id": "7", "chapter_id": "ch3"},
                ),
            ),
            total_cost=0.5,
            total_duration_seconds=1.2,
            errors=(),
        )
        await store.put(job)
        loaded = await store.get("job-1")
        assert loaded is not None
        assert loaded.result is not None
        assert loaded.result.outputs[0].metadata == {"sequence_id": "7", "chapter_id": "ch3"}

    @pytest.mark.parametrize("metadata_json", ['"not-a-dict"', "null", "123", "[]", "{}"])
    @pytest.mark.asyncio
    async def test_result_with_non_dict_metadata_falls_back_to_empty(
        self,
        store: RedisJobStore,
        redis_url: str,
        metadata_json: str,
    ) -> None:
        """CORR-035: a non-dict metadata value must not crash; fall back to {}."""
        from acheron.shell.stores.redis import _JOB_KEY

        r = aioredis.Redis.from_url(redis_url)
        try:
            blob = (
                '{"job_id":"j-bad","source_type":"epub",'
                '"request":{"source_path":"/x","source_language":"en","target_language":"es"},'
                '"strategy":"streaming","status":"failed","label":null,'
                '"retries_from":null,"created_at":"2026-07-29T12:00:00+00:00",'
                '"last_persisted_at":"2026-07-29T12:00:00+00:00",'
                '"progress":{"completed_steps":0,"total_steps":0,"current_step_id":null,'
                '"current_worker_type":null,"current_worker_id":null,"eta_seconds":null,'
                '"successful_duration_seconds":0.0},'
                '"plan":null,'
                '"result":{"plan_id":"p","status":"failed","completed_steps":1,'
                '"total_steps":1,"outputs":[{"path":"/x","filename":"x","size_bytes":1,'
                '"checksum":"c","content_type":"audio/wav","metadata":' + metadata_json + "}],"
                '"total_cost":0.0,"total_duration_seconds":0.0,"errors":[]}}'
            )
            await r.set(_JOB_KEY.format(job_id="j-bad"), blob)
        finally:
            await r.aclose()
        loaded = await store.get("j-bad")
        assert loaded is not None
        assert loaded.result is not None
        assert loaded.result.outputs[0].metadata == {}


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

    @pytest.mark.asyncio
    async def test_list_returns_jobs_in_sorted_id_order(self, store: RedisJobStore) -> None:
        """REPRO-001: Redis set iteration is non-deterministic; list_all must
        sort the ids so worker/job selection is reproducible across calls.
        """
        await store.put(_tracked("zeta"))
        await store.put(_tracked("alpha"))
        await store.put(_tracked("mu"))
        first = await store.list_all()
        second = await store.list_all()
        assert [j.job_id for j in first] == ["alpha", "mu", "zeta"]
        assert [j.job_id for j in second] == ["alpha", "mu", "zeta"]


class TestFailFast:
    @pytest.mark.asyncio
    async def test_unreachable_redis_raises_on_connect(self) -> None:
        store = RedisJobStore("redis://localhost:1")
        with pytest.raises((RedisConnectionError, redis.RedisError)):
            await store.connect()


class TestFailureNormalization:
    @pytest.mark.asyncio
    async def test_put_chains_redis_error_as_store_error(self) -> None:
        class _FailingPipeline:
            async def __aenter__(self) -> Self:
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

            def set(self, *_args: object, **_kwargs: object) -> _FailingPipeline:
                return self

            def sadd(self, *_args: object, **_kwargs: object) -> _FailingPipeline:
                return self

            async def execute(self) -> list[object]:
                raise redis.RedisError("pipeline unavailable")

        class _FailingRedis:
            def pipeline(self, **_kwargs: object) -> _FailingPipeline:
                return _FailingPipeline()

        store = object.__new__(RedisJobStore)
        store._redis = cast("_RedisAwaitable", _FailingRedis())  # noqa: SLF001

        with pytest.raises(StoreError) as exc_info:
            await store.put(_tracked())

        assert isinstance(exc_info.value.__cause__, redis.RedisError)
