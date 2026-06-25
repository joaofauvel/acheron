"""Tests for the step handler."""

from __future__ import annotations

from pathlib import Path

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
from acheron.shell.cache import StepCache
from acheron.shell.local_handlers import LocalJobHandler
from acheron.shell.registry import RegisteredWorker
from acheron.shell.step_handler import create_step_handler, default_worker_factory
from acheron.shell.stores.memory import InMemoryWorkerStore
from acheron.shell.transports.http import HttpWorker
from acheron.shell.transports.local import LocalWorker

_TEST_DATA_DIR = Path("/tmp/acheron-test-step-handler")


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
        handler = create_step_handler(reg, worker_factory=lambda _reg: local_worker, data_dir=_TEST_DATA_DIR)
        plan = _make_plan()
        step = plan.steps[0]
        result = await handler(step, plan)
        assert result.status == JobStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_raises_when_no_worker_found(self) -> None:
        reg = InMemoryWorkerStore()
        handler = create_step_handler(reg, data_dir=_TEST_DATA_DIR)
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

        handler = create_step_handler(reg, worker_factory=_factory, data_dir=_TEST_DATA_DIR)
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

    @pytest.mark.asyncio
    async def test_list_all_cached_per_plan(self) -> None:
        """registry.list_all() is called once when handling multiple steps of the same plan."""
        call_count = 0

        class CountingStore(InMemoryWorkerStore):
            async def list_all(self) -> tuple[RegisteredWorker, ...]:
                nonlocal call_count
                call_count += 1
                return await super().list_all()

        reg = CountingStore()
        await reg.register("tts-1", "http://127.0.0.1:1", "http", _tts_caps())
        local_worker = LocalWorker(
            worker_type=WorkerType.TTS,
            handler=_echo_job_result,
            supported_languages_in=frozenset({"es"}),
            supported_languages_out=frozenset({"es"}),
        )
        handler = create_step_handler(reg, worker_factory=lambda _reg: local_worker, data_dir=_TEST_DATA_DIR)
        plan = Plan(
            plan_id="plan-1",
            job_id="job-1",
            source_type="epub",
            source_language="en",
            target_language="es",
            executor_strategy=ExecutorStrategy.STREAMING,
            steps=(
                PlanStep(
                    step_id="s1",
                    type=WorkerType.TTS,
                    depends_on=(),
                    status=StepStatus.PENDING,
                    payload={"target_language": "es", "chapter_id": "ch1"},
                ),
                PlanStep(
                    step_id="s2",
                    type=WorkerType.TTS,
                    depends_on=("s1",),
                    status=StepStatus.PENDING,
                    payload={"target_language": "es", "chapter_id": "ch2"},
                ),
            ),
        )
        await handler(plan.steps[0], plan)
        await handler(plan.steps[1], plan)
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_worker_factory_called_once_per_worker_id(self) -> None:
        """worker_factory is called once per worker_id across multiple steps."""
        reg = InMemoryWorkerStore()
        await reg.register("tts-1", "http://127.0.0.1:1", "http", _tts_caps())
        factory_calls: list[str] = []
        worker = LocalWorker(
            worker_type=WorkerType.TTS,
            handler=_echo_job_result,
            supported_languages_in=frozenset({"es"}),
            supported_languages_out=frozenset({"es"}),
        )

        def _factory(registered: object) -> LocalWorker:
            factory_calls.append(getattr(registered, "worker_id", ""))
            return worker

        handler = create_step_handler(reg, worker_factory=_factory, data_dir=_TEST_DATA_DIR)
        plan = Plan(
            plan_id="plan-1",
            job_id="job-1",
            source_type="epub",
            source_language="en",
            target_language="es",
            executor_strategy=ExecutorStrategy.STREAMING,
            steps=(
                PlanStep(
                    step_id="s1",
                    type=WorkerType.TTS,
                    depends_on=(),
                    status=StepStatus.PENDING,
                    payload={"target_language": "es", "chapter_id": "ch1"},
                ),
                PlanStep(
                    step_id="s2",
                    type=WorkerType.TTS,
                    depends_on=("s1",),
                    status=StepStatus.PENDING,
                    payload={"target_language": "es", "chapter_id": "ch2"},
                ),
            ),
        )
        await handler(plan.steps[0], plan)
        await handler(plan.steps[1], plan)
        assert factory_calls == ["tts-1"]


class TestHttpWorkerStepCache:
    """ARCH-015: ``HttpWorker`` constructs its own ``StepCache`` from ``data_dir``
    when the factory does not pass one. The factory is no longer the seam."""

    def test_default_worker_factory_produces_http_worker_with_default_step_cache(self) -> None:
        from acheron.shell.registry import RegisteredWorker

        reg = RegisteredWorker(
            worker_id="tts-x",
            endpoint="http://worker:8000",
            transport="http",
            capabilities=_tts_caps(),
        )
        worker = default_worker_factory(reg, data_dir=_TEST_DATA_DIR)
        assert isinstance(worker, HttpWorker)
        assert isinstance(worker._step_cache, StepCache)  # noqa: SLF001
        assert worker._step_cache.data_dir == Path(_TEST_DATA_DIR)  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_create_step_handler_default_lambda_produces_http_worker_with_default_step_cache(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The default factory lambda in ``create_step_handler`` (no
        ``worker_factory`` arg) must produce an ``HttpWorker`` whose
        ``_step_cache`` is the worker's own default (constructed from
        ``data_dir``). The orchestrator no longer threads ``step_cache``
        through the factory (ARCH-015)."""
        import acheron.shell.step_handler as sh

        reg = InMemoryWorkerStore()
        await reg.register("tts-1", "http://127.0.0.1:1", "http", _tts_caps())
        handler = create_step_handler(reg, data_dir=_TEST_DATA_DIR)
        original_default = default_worker_factory
        captured: dict = {}

        def _capturing_default(
            registered: RegisteredWorker,
            local_handlers: dict[str, LocalJobHandler] | None = None,
            *,
            data_dir: Path | str = _TEST_DATA_DIR,
        ) -> object:
            worker = original_default(registered, local_handlers, data_dir=data_dir)
            captured["worker"] = worker
            worker.execute = _echo_job_result  # type: ignore[method-assign]
            return worker

        monkeypatch.setattr(sh, "default_worker_factory", _capturing_default)
        await handler(_make_plan().steps[0], _make_plan())
        assert isinstance(captured["worker"], HttpWorker)
        assert isinstance(captured["worker"]._step_cache, StepCache)  # noqa: SLF001
        assert sh.default_worker_factory is _capturing_default
        # monkeypatch restores the module-level original on test teardown.
        assert captured["worker"]._step_cache.data_dir == Path(_TEST_DATA_DIR)  # noqa: SLF001
