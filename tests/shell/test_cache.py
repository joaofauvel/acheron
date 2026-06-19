"""Tests for plan and step caching."""

import hashlib
from pathlib import Path

import pytest
import pytest_asyncio

from acheron.core.errors import CacheMissError
from acheron.core.models import (
    ExecutorStrategy,
    OutputFile,
    Plan,
    PlanStep,
    StepStatus,
    WorkerType,
)
from acheron.shell.cache import PlanCache, StepCache


def _sample_plan(plan_id: str = "plan-1") -> Plan:
    return Plan(
        plan_id=plan_id,
        job_id="job-1",
        source_type="epub",
        source_language="en",
        target_language="es",
        executor_strategy=ExecutorStrategy.BATCH_ASYNC,
        steps=(
            PlanStep(
                step_id="extract",
                type=WorkerType.EXTRACTION,
                depends_on=(),
                status=StepStatus.PENDING,
                payload={"source_path": "/input/book.epub"},
            ),
            PlanStep(
                step_id="chunk-ch1",
                type=WorkerType.CHUNKING,
                depends_on=("extract",),
                status=StepStatus.PENDING,
                payload={"chapter_id": "ch1"},
            ),
        ),
    )


class TestPlanCache:
    def test_save_and_load(self, tmp_path: Path) -> None:
        cache = PlanCache(tmp_path)
        plan = _sample_plan()
        cache.save_plan(plan)
        loaded = cache.load_plan("plan-1")
        assert loaded.plan_id == plan.plan_id
        assert loaded.job_id == plan.job_id
        assert loaded.source_type == plan.source_type
        assert len(loaded.steps) == 2
        assert loaded.steps[0].step_id == "extract"
        assert loaded.steps[0].type == WorkerType.EXTRACTION
        assert loaded.steps[0].depends_on == ()
        assert loaded.steps[0].status == StepStatus.PENDING

    def test_plan_exists_true(self, tmp_path: Path) -> None:
        cache = PlanCache(tmp_path)
        cache.save_plan(_sample_plan())
        assert cache.plan_exists("plan-1")

    def test_plan_exists_false(self, tmp_path: Path) -> None:
        cache = PlanCache(tmp_path)
        assert not cache.plan_exists("nope")

    def test_load_nonexistent_raises(self, tmp_path: Path) -> None:
        cache = PlanCache(tmp_path)
        with pytest.raises(CacheMissError):
            cache.load_plan("nope")

    def test_save_creates_directory(self, tmp_path: Path) -> None:
        cache = PlanCache(tmp_path)
        cache.save_plan(_sample_plan())
        assert (tmp_path / "plan-1" / "plan.json").exists()

    def test_round_trip_preserves_batch_flag(self, tmp_path: Path) -> None:
        cache = PlanCache(tmp_path)
        plan = Plan(
            plan_id="p-2",
            job_id="j-2",
            source_type="epub",
            source_language="en",
            target_language="es",
            executor_strategy=ExecutorStrategy.BATCH_ASYNC,
            steps=(
                PlanStep(
                    step_id="tts",
                    type=WorkerType.TTS,
                    depends_on=(),
                    status=StepStatus.PENDING,
                    payload={},
                    batch=True,
                ),
            ),
        )
        cache.save_plan(plan)
        loaded = cache.load_plan("p-2")
        assert loaded.steps[0].batch is True


class TestStepCache:
    @pytest_asyncio.fixture
    async def cache(self, tmp_path: Path) -> StepCache:
        return StepCache(tmp_path)

    @pytest.mark.asyncio
    async def test_save_and_load_outputs(self, cache: StepCache) -> None:
        outputs = (
            OutputFile(
                path="/data/out/chunk-0.wav",
                filename="chunk-0.wav",
                size_bytes=44100,
                checksum="abc123",
                content_type="audio/wav",
            ),
        )
        await cache.save_outputs("job-1", "tts-ch1", outputs)
        loaded = await cache.load_outputs("job-1", "tts-ch1")
        assert len(loaded) == 1
        assert loaded[0].filename == "chunk-0.wav"
        assert loaded[0].checksum == "abc123"

    @pytest.mark.asyncio
    async def test_load_nonexistent_raises(self, cache: StepCache) -> None:
        with pytest.raises(CacheMissError):
            await cache.load_outputs("job-1", "nope")

    @pytest.mark.asyncio
    async def test_step_has_valid_cache_true(self, tmp_path: Path, cache: StepCache) -> None:
        test_file = tmp_path / "output.wav"
        test_file.write_bytes(b"audio data")
        checksum = hashlib.sha256(b"audio data").hexdigest()
        outputs = (
            OutputFile(
                path=str(test_file),
                filename="output.wav",
                size_bytes=10,
                checksum=checksum,
                content_type="audio/wav",
            ),
        )
        await cache.save_outputs("job-1", "tts-ch1", outputs)
        assert await cache.step_has_valid_cache("job-1", "tts-ch1")

    @pytest.mark.asyncio
    async def test_step_has_valid_cache_missing_manifest(self, cache: StepCache) -> None:
        assert not await cache.step_has_valid_cache("job-1", "nope")

    @pytest.mark.asyncio
    async def test_step_has_valid_cache_corrupted_checksum(
        self, tmp_path: Path, cache: StepCache
    ) -> None:
        test_file = tmp_path / "output.wav"
        test_file.write_bytes(b"audio data")
        outputs = (
            OutputFile(
                path=str(test_file),
                filename="output.wav",
                size_bytes=10,
                checksum="bad_checksum",
                content_type="audio/wav",
            ),
        )
        await cache.save_outputs("job-1", "tts-ch1", outputs)
        assert not await cache.step_has_valid_cache("job-1", "tts-ch1")

    @pytest.mark.asyncio
    async def test_step_has_valid_cache_missing_file(self, cache: StepCache) -> None:
        outputs = (
            OutputFile(
                path="/nonexistent/output.wav",
                filename="output.wav",
                size_bytes=10,
                checksum="abc",
                content_type="audio/wav",
            ),
        )
        await cache.save_outputs("job-1", "tts-ch1", outputs)
        assert not await cache.step_has_valid_cache("job-1", "tts-ch1")
