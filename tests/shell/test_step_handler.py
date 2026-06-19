"""Tests for the step handler."""

from __future__ import annotations

import pytest

from acheron.core.errors import WorkerError
from acheron.core.models import (
    ExecutorStrategy,
    JobMetrics,
    JobResult,
    JobStatus,
    Plan,
    PlanStep,
    StepStatus,
    WorkerCapabilities,
    WorkerType,
)
from acheron.shell.step_handler import create_step_handler
from acheron.shell.stores.memory import InMemoryWorkerStore
from acheron.shell.transports.local import LocalWorker


async def _echo_job_result(job: object) -> JobResult:
    return JobResult(
        job_id="j-1",
        status=JobStatus.SUCCESS,
        outputs=(),
        metrics=JobMetrics(duration_seconds=0.1),
    )


def _tts_caps() -> WorkerCapabilities:
    return WorkerCapabilities(
        worker_type=WorkerType.TTS,
        supported_languages_in=frozenset({"es"}),
        supported_languages_out=frozenset({"es"}),
        supported_formats_in=frozenset({"text"}),
        supported_formats_out=frozenset({"wav"}),
        max_payload_bytes=None,
        batch_capable=True,
        model_source=None,
    )


def _make_plan() -> Plan:
    return Plan(
        plan_id="plan-1",
        job_id="job-1",
        source_type="epub",
        source_language="en",
        target_language="es",
        executor_strategy=ExecutorStrategy.BATCH_ASYNC,
        steps=(
            PlanStep(
                step_id="synthesize",
                type=WorkerType.TTS,
                depends_on=(),
                status=StepStatus.PENDING,
                payload={"target_language": "es", "chapter_id": "ch1"},
            ),
        ),
    )


class TestStepHandler:
    @pytest.mark.asyncio
    async def test_dispatches_to_matching_worker(self) -> None:
        reg = InMemoryWorkerStore()
        local_worker = LocalWorker(
            worker_type=WorkerType.TTS,
            handler=_echo_job_result,
            supported_languages_in=frozenset({"es"}),
            supported_languages_out=frozenset({"es"}),
        )
        await reg.register("tts-1", "http://tts", "http", _tts_caps())
        handler = create_step_handler(reg, worker_factory=lambda _reg: local_worker)
        plan = _make_plan()
        step = plan.steps[0]
        result = await handler(step, plan)
        assert result.status == JobStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_raises_when_no_worker_found(self) -> None:
        reg = InMemoryWorkerStore()
        handler = create_step_handler(reg)
        plan = _make_plan()
        step = plan.steps[0]
        with pytest.raises(WorkerError, match="No worker"):
            await handler(step, plan)
