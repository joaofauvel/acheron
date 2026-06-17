"""Tests for plan and step caching."""

from pathlib import Path

import pytest

from acheron.core.errors import CacheMissError
from acheron.core.models import (
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
        executor_strategy="batch_async",
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
            executor_strategy="batch_async",
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
    def test_save_and_load_outputs(self, tmp_path: Path) -> None:
        cache = StepCache(tmp_path)
        outputs = (
            OutputFile(
                path="/data/out/chunk-0.wav",
                filename="chunk-0.wav",
                size_bytes=44100,
                checksum="abc123",
                content_type="audio/wav",
            ),
        )
        cache.save_outputs("job-1", "tts-ch1", outputs)
        loaded = cache.load_outputs("job-1", "tts-ch1")
        assert len(loaded) == 1
        assert loaded[0].filename == "chunk-0.wav"
        assert loaded[0].checksum == "abc123"

    def test_load_nonexistent_raises(self, tmp_path: Path) -> None:
        cache = StepCache(tmp_path)
        with pytest.raises(CacheMissError):
            cache.load_outputs("job-1", "nope")

    def test_step_has_valid_cache_true(self, tmp_path: Path) -> None:
        test_file = tmp_path / "output.wav"
        test_file.write_bytes(b"audio data")
        import hashlib

        checksum = hashlib.sha256(b"audio data").hexdigest()
        cache = StepCache(tmp_path)
        outputs = (
            OutputFile(
                path=str(test_file),
                filename="output.wav",
                size_bytes=10,
                checksum=checksum,
                content_type="audio/wav",
            ),
        )
        cache.save_outputs("job-1", "tts-ch1", outputs)
        assert cache.step_has_valid_cache("job-1", "tts-ch1")

    def test_step_has_valid_cache_missing_manifest(self, tmp_path: Path) -> None:
        cache = StepCache(tmp_path)
        assert not cache.step_has_valid_cache("job-1", "nope")

    def test_step_has_valid_cache_corrupted_checksum(self, tmp_path: Path) -> None:
        test_file = tmp_path / "output.wav"
        test_file.write_bytes(b"audio data")
        cache = StepCache(tmp_path)
        outputs = (
            OutputFile(
                path=str(test_file),
                filename="output.wav",
                size_bytes=10,
                checksum="bad_checksum",
                content_type="audio/wav",
            ),
        )
        cache.save_outputs("job-1", "tts-ch1", outputs)
        assert not cache.step_has_valid_cache("job-1", "tts-ch1")

    def test_step_has_valid_cache_missing_file(self, tmp_path: Path) -> None:
        cache = StepCache(tmp_path)
        outputs = (
            OutputFile(
                path="/nonexistent/output.wav",
                filename="output.wav",
                size_bytes=10,
                checksum="abc",
                content_type="audio/wav",
            ),
        )
        cache.save_outputs("job-1", "tts-ch1", outputs)
        assert not cache.step_has_valid_cache("job-1", "tts-ch1")
