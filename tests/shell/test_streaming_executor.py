"""Tests for the StreamingExecutor."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from acheron.core.models import (
    ExecutorStrategy,
    JobMetrics,
    JobResult,
    JobStatus,
    OutputFile,
    Plan,
    PlanStep,
    StepStatus,
    WorkerType,
)
from acheron.shell.cache import StepCache
from acheron.shell.executors._utils import StepHandler
from acheron.shell.executors.streaming import StreamingExecutor


def _real_output(tmp_path: Path, name: str, body: bytes = b"x" * 16) -> OutputFile:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(body)
    return OutputFile(
        path=str(p),
        filename=name,
        size_bytes=len(body),
        checksum="placeholder",
        content_type="audio/wav",
    )


def _make_handler(
    outputs: dict[str, list[OutputFile]],
) -> tuple[StepHandler, list[str]]:
    calls: list[str] = []

    async def handler(step: PlanStep, plan: Plan) -> JobResult:
        calls.append(step.step_id)
        await asyncio.sleep(0)
        return JobResult(
            job_id=plan.job_id,
            status=JobStatus.SUCCESS,
            outputs=tuple(outputs[step.step_id]),
            metrics=JobMetrics(duration_seconds=0.0),
        )

    return handler, calls


def _linear_plan(job_id: str = "job-1", plan_id: str = "plan-1") -> Plan:
    return Plan(
        plan_id=plan_id,
        job_id=job_id,
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
                payload={"source_path": "/tmp/x"},
            ),
            PlanStep(
                step_id="chunk",
                type=WorkerType.CHUNKING,
                depends_on=("extract",),
                status=StepStatus.PENDING,
                payload={},
            ),
            PlanStep(
                step_id="package",
                type=WorkerType.PACKAGING,
                depends_on=("chunk",),
                status=StepStatus.PENDING,
                payload={},
            ),
        ),
    )


@pytest_asyncio.fixture
async def step_cache(tmp_path: Path) -> StepCache:
    return StepCache(tmp_path)


class TestNormalCompletion:
    @pytest.mark.asyncio
    async def test_three_step_plan_completes(self, tmp_path: Path, step_cache: StepCache) -> None:
        plan = _linear_plan()
        outputs = {
            "extract": [_real_output(tmp_path, "extracted.txt")],
            "chunk": [_real_output(tmp_path, "chunked.txt")],
            "package": [_real_output(tmp_path, "out.wav", body=b"audio-bytes")],
        }
        handler, calls = _make_handler(outputs)
        executor = StreamingExecutor(handler, step_cache)

        result = await executor.run(plan)

        assert result.status == "completed"
        assert result.completed_steps == 3
        assert result.total_steps == 3
        assert calls == ["extract", "chunk", "package"]
        assert len(result.outputs) == 3
        filenames = {o.filename for o in result.outputs}
        assert filenames == {"extracted.txt", "chunked.txt", "out.wav"}


class TestStepTimeout:
    @pytest.mark.asyncio
    async def test_slow_handler_raises_worker_error(self, tmp_path: Path, step_cache: StepCache) -> None:
        plan = _linear_plan()

        async def slow_handler(step: PlanStep, plan: Plan) -> JobResult:
            await asyncio.sleep(0.5)
            return JobResult(
                job_id=plan.job_id,
                status=JobStatus.SUCCESS,
                outputs=(_real_output(tmp_path, "out.wav"),),
                metrics=JobMetrics(duration_seconds=0.0),
            )

        executor = StreamingExecutor(slow_handler, step_cache, step_timeout=0.05)
        result = await executor.run(plan)

        assert result.status == "failed"
        assert "timed out" in result.errors[0].lower()


class TestWorkerError:
    @pytest.mark.asyncio
    async def test_worker_unavailable_propagates(self, tmp_path: Path, step_cache: StepCache) -> None:
        from acheron.core.errors import WorkerUnavailableError

        plan = _linear_plan()

        async def failing_handler(step: PlanStep, plan: Plan) -> JobResult:
            msg = f"no worker for {step.step_id}"
            raise WorkerUnavailableError(msg)

        executor = StreamingExecutor(failing_handler, step_cache)
        result = await executor.run(plan)

        assert result.status == "failed"
        assert any("no worker" in e.lower() for e in result.errors)


class TestUnexpectedException:
    @pytest.mark.asyncio
    async def test_unhandled_exception_wrapped_as_pipeline_error(self, tmp_path: Path, step_cache: StepCache) -> None:
        plan = _linear_plan()

        async def bad_handler(step: PlanStep, plan: Plan) -> JobResult:
            msg = "boom"
            raise RuntimeError(msg)

        executor = StreamingExecutor(bad_handler, step_cache)
        result = await executor.run(plan)

        assert result.status == "failed"
        assert any("boom" in e.lower() or "streaming" in e.lower() for e in result.errors)


class TestCacheFailure:
    @pytest.mark.asyncio
    async def test_save_outputs_failure_wrapped_as_pipeline_error(self, tmp_path: Path, step_cache: StepCache) -> None:
        plan = _linear_plan()

        async def ok_handler(step: PlanStep, plan: Plan) -> JobResult:
            return JobResult(
                job_id=plan.job_id,
                status=JobStatus.SUCCESS,
                outputs=(_real_output(tmp_path, "out.wav"),),
                metrics=JobMetrics(duration_seconds=0.0),
            )

        executor = StreamingExecutor(ok_handler, step_cache)

        async def broken_save(*_args: object, **_kwargs: object) -> None:
            msg = "disk full"
            raise OSError(msg)

        step_cache.save_outputs = broken_save  # type: ignore[method-assign]

        result = await executor.run(plan)

        assert result.status == "failed"
        assert any("save_outputs" in e.lower() for e in result.errors)


class TestTaskGroupCancellation:
    @pytest.mark.asyncio
    async def test_middle_failure_cancels_upstream_and_downstream(self, tmp_path: Path, step_cache: StepCache) -> None:
        from acheron.core.errors import WorkerUnavailableError

        plan = _linear_plan()
        started: list[str] = []

        async def handler(step: PlanStep, plan: Plan) -> JobResult:
            started.append(step.step_id)
            if step.step_id == "chunk":
                raise WorkerUnavailableError("chunk failed")
            # Slow downstream so we can observe cancellation.
            await asyncio.sleep(1.0)
            return JobResult(
                job_id=plan.job_id,
                status=JobStatus.SUCCESS,
                outputs=(_real_output(tmp_path, f"{step.step_id}.out"),),
                metrics=JobMetrics(duration_seconds=0.0),
            )

        executor = StreamingExecutor(handler, step_cache, step_timeout=5.0)
        result = await executor.run(plan)

        assert result.status == "failed"
        assert "extract" in started
        assert "chunk" in started
        package_manifest = step_cache._data_dir / plan.job_id / "package" / "manifest.json"  # noqa: SLF001
        assert not package_manifest.exists()


class TestSentinelDrain:
    @pytest.mark.asyncio
    async def test_sentinel_propagates_downstream(self, tmp_path: Path, step_cache: StepCache) -> None:
        from acheron.core.errors import WorkerUnavailableError

        plan = _linear_plan()
        completed: list[str] = []

        async def handler(step: PlanStep, plan: Plan) -> JobResult:
            if step.step_id == "extract":
                raise WorkerUnavailableError("extract failed")
            completed.append(step.step_id)
            return JobResult(
                job_id=plan.job_id,
                status=JobStatus.SUCCESS,
                outputs=(_real_output(tmp_path, f"{step.step_id}.out"),),
                metrics=JobMetrics(duration_seconds=0.0),
            )

        executor = StreamingExecutor(handler, step_cache)
        result = await executor.run(plan)

        assert result.status == "failed"
        assert completed == []


class TestOutputsFromCache:
    @pytest.mark.asyncio
    async def test_outputs_match_cache_contents(self, tmp_path: Path, step_cache: StepCache) -> None:
        plan = _linear_plan()
        outputs = {
            "extract": [_real_output(tmp_path, "extracted.txt", body=b"e" * 8)],
            "chunk": [_real_output(tmp_path, "chunk1.txt"), _real_output(tmp_path, "chunk2.txt")],
            "package": [_real_output(tmp_path, "out.wav", body=b"a" * 1024)],
        }
        handler, _ = _make_handler(outputs)
        executor = StreamingExecutor(handler, step_cache)

        result = await executor.run(plan)

        assert len(result.outputs) == 4
        for step in plan.steps:
            cached = await step_cache.load_outputs(plan.job_id, step.step_id)
            assert len(cached) == len(outputs[step.step_id])
