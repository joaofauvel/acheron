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
    async def test_three_step_plan_completes(
        self, tmp_path: Path, step_cache: StepCache
    ) -> None:
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
