"""Tests for plan and step caching."""

import hashlib
from pathlib import Path

import pytest
import pytest_asyncio

from acheron.core.errors import CacheError, CacheMissError
from acheron.core.models import (
    ExecutorStrategy,
    OutputFile,
    Plan,
    PlanStep,
    StepStatus,
    WorkerType,
)
from acheron.shell.cache import InMemoryStepCache, PlanCache, StepCache


def _sample_plan(plan_id: str = "plan-abcd1234") -> Plan:
    return Plan(
        plan_id=plan_id,
        job_id="job-1",
        source_type="epub",
        source_language="en",
        target_language="es",
        executor_strategy=ExecutorStrategy.STREAMING,
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
        loaded = cache.load_plan("plan-abcd1234")
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
        assert cache.plan_exists("plan-abcd1234")

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
        assert (tmp_path / "plan-abcd1234" / "plan.json").exists()

    def test_delete_plan_returns_size_and_is_idempotent(self, tmp_path: Path) -> None:
        cache = PlanCache(tmp_path)
        cache.save_plan(_sample_plan())
        expected = (tmp_path / "plan-abcd1234" / "plan.json").stat().st_size
        assert cache.delete_plan("plan-abcd1234") == expected
        assert cache.delete_plan("plan-abcd1234") == 0

    def test_load_rejects_plan_id_path_escape(self, tmp_path: Path) -> None:
        """A plan_id with traversal segments must raise CacheMissError before touching the filesystem."""
        outside = tmp_path.parent / "escaped-plan"
        outside.mkdir()
        (outside / "plan.json").write_text("{}")

        with pytest.raises(CacheMissError):
            PlanCache(tmp_path).load_plan("../escaped-plan")


class TestStepCache:
    @pytest_asyncio.fixture
    async def cache(self, tmp_path: Path) -> StepCache:
        return StepCache(tmp_path)

    @pytest.mark.asyncio
    async def test_delete_job_returns_size_and_is_idempotent(self, cache: StepCache) -> None:
        await cache.save_outputs("job-1", "step-1", ())
        expected = (cache.data_dir / "job-1" / "step-1" / "manifest.json").stat().st_size
        assert await cache.job_size("job-1") == expected
        assert await cache.delete_job("job-1") == expected
        assert await cache.delete_job("job-1") == 0

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
    async def test_step_has_valid_cache_corrupted_checksum(self, tmp_path: Path, cache: StepCache) -> None:
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

    @pytest.mark.asyncio
    async def test_invalidate_steps_removes_only_selected_manifests(self, cache: StepCache) -> None:
        outputs = (
            OutputFile(
                path="/nonexistent/output.wav",
                filename="output.wav",
                size_bytes=10,
                checksum="abc",
                content_type="audio/wav",
            ),
        )
        await cache.save_outputs("job-1", "step-46", outputs)
        await cache.save_outputs("job-1", "step-47", outputs)
        await cache.save_outputs("job-1", "step-48", outputs)

        await cache.invalidate_steps("job-1", {"step-47", "step-48"})

        assert (cache.data_dir / "job-1" / "step-46" / "manifest.json").exists()
        assert not (cache.data_dir / "job-1" / "step-47" / "manifest.json").exists()
        assert not (cache.data_dir / "job-1" / "step-48" / "manifest.json").exists()

    @pytest.mark.asyncio
    async def test_inmemory_invalidate_steps_removes_only_selected_manifests(self) -> None:
        cache = InMemoryStepCache()
        outputs = (
            OutputFile(
                path="/nonexistent/output.wav",
                filename="output.wav",
                size_bytes=10,
                checksum="abc",
                content_type="audio/wav",
            ),
        )
        await cache.save_outputs("job-1", "step-46", outputs)
        await cache.save_outputs("job-1", "step-47", outputs)

        await cache.invalidate_steps("job-1", {"step-47"})

        with pytest.raises(CacheMissError):
            await cache.load_outputs("job-1", "step-47")
        assert await cache.load_outputs("job-1", "step-46") == outputs

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad_part", ["/tmp/out", "../out", "step/../out", "step\\\\out", ".", ".."])
    async def test_file_cache_rejects_unsafe_invalidation_parts(self, cache: StepCache, bad_part: str) -> None:
        with pytest.raises(CacheError):
            await cache.invalidate_steps(bad_part, {"step-1"})
        with pytest.raises(CacheError):
            await cache.invalidate_steps("job-1", {bad_part})

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad_part", ["/tmp/out", "../out", "step/../out", "step\\\\out", ".", ".."])
    async def test_inmemory_cache_rejects_unsafe_invalidation_parts(self, bad_part: str) -> None:
        cache = InMemoryStepCache()
        with pytest.raises(CacheError):
            await cache.invalidate_steps(bad_part, {"step-1"})
        with pytest.raises(CacheError):
            await cache.invalidate_steps("job-1", {bad_part})
