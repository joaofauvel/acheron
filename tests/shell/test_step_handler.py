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
        executor_strategy=ExecutorStrategy.STREAMING,
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
        await reg.register("tts-1", "http://127.0.0.1:1", "http", _tts_caps())
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

    @pytest.mark.asyncio
    async def test_asr_worker_missing_output_language_is_skipped(self) -> None:
        """When two ASR workers are registered and the first has the source
        language in its input set but NOT in its output set, the handler must
        skip it and select the second worker (matching the planner's check)."""
        reg = InMemoryWorkerStore()
        chosen_worker_id: list[str] = []

        async def _echo(_job: object) -> JobResult:
            return JobResult(
                job_id="j-1",
                status=JobStatus.SUCCESS,
                outputs=(),
                metrics=JobMetrics(duration_seconds=0.0),
            )

        def _factory(registered: object) -> LocalWorker:
            wid = getattr(registered, "worker_id", "")
            chosen_worker_id.append(wid)
            caps: WorkerCapabilities = registered.capabilities  # type: ignore[attr-defined]
            return LocalWorker(
                worker_type=WorkerType.ASR,
                handler=_echo,
                supported_languages_in=caps.supported_languages_in,
                supported_languages_out=caps.supported_languages_out,
            )

        await reg.register(
            "asr-bad",
            "http://127.0.0.1:1",
            "http",
            WorkerCapabilities(
                worker_type=WorkerType.ASR,
                supported_languages_in=frozenset({"en"}),
                supported_languages_out=frozenset(),
                supported_formats_in=frozenset({"wav"}),
                supported_formats_out=frozenset({"text"}),
                max_payload_bytes=None,
                batch_capable=False,
                model_source=None,
            ),
        )
        await reg.register(
            "asr-good",
            "http://127.0.0.1:2",
            "http",
            WorkerCapabilities(
                worker_type=WorkerType.ASR,
                supported_languages_in=frozenset({"en"}),
                supported_languages_out=frozenset({"en"}),
                supported_formats_in=frozenset({"wav"}),
                supported_formats_out=frozenset({"text"}),
                max_payload_bytes=None,
                batch_capable=False,
                model_source=None,
            ),
        )

        handler = create_step_handler(reg, worker_factory=_factory)
        plan = Plan(
            plan_id="p",
            job_id="j",
            source_type="audio",
            source_language="en",
            target_language="en",
            executor_strategy=ExecutorStrategy.STREAMING,
            steps=(
                PlanStep(
                    step_id="transcribe",
                    type=WorkerType.ASR,
                    depends_on=(),
                    status=StepStatus.PENDING,
                    payload={},
                ),
            ),
        )
        await handler(plan.steps[0], plan)
        assert chosen_worker_id == ["asr-good"]
