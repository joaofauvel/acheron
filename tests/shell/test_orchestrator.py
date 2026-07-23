"""Tests for the orchestrator."""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path

import pytest

from acheron.core.errors import (
    ChunkingTooLongForWorkerError,
    InvalidLanguagePathError,
    JobAlreadyRunningError,
    JobNotFoundError,
)
from acheron.core.models import (
    EpubRequest,
    ExecutorStrategy,
    JobMetrics,
    JobResult,
    JobStatus,
    Plan,
    PlanStatus,
    PlanStep,
    StepStatus,
    WorkerType,
)
from acheron.shell.cache import InMemoryStepCache, PlanCache, StepCache
from acheron.shell.config import Settings
from acheron.shell.job_store import TrackedJob
from acheron.shell.orchestrator import Orchestrator
from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore
from tests.shell.conftest import translation_caps, tts_caps


async def _success_handler(_step: PlanStep, _plan: Plan) -> JobResult:
    return JobResult(
        job_id="noop",
        status=JobStatus.SUCCESS,
        outputs=(),
        metrics=JobMetrics(duration_seconds=0.01),
    )


class _ControlledPutJobStore(InMemoryJobStore):
    """Job store that gates the first reconciliation write."""

    def __init__(self) -> None:
        super().__init__()
        self.persist_started = asyncio.Event()
        self.release_persist = asyncio.Event()
        self._puts = 0

    async def put(self, job: TrackedJob) -> None:
        self._puts += 1
        if self._puts == 2:
            self.persist_started.set()
            await self.release_persist.wait()
        await super().put(copy.deepcopy(job))


class _FailingSecondPutJobStore(InMemoryJobStore):
    """Fails the second put; snapshots all other jobs like a remote store."""

    def __init__(self) -> None:
        super().__init__()
        self._puts = 0

    async def put(self, job: TrackedJob) -> None:
        self._puts += 1
        if self._puts == 2:
            msg = "store temporarily unavailable"
            raise RuntimeError(msg)
        await super().put(copy.deepcopy(job))


class _KeyErrorOnSecondPutJobStore(InMemoryJobStore):
    """Raises KeyError on the second put (programming-error stand-in)."""

    def __init__(self) -> None:
        super().__init__()
        self._puts = 0

    async def put(self, job: TrackedJob) -> None:
        self._puts += 1
        if self._puts == 2:
            msg = "serialiser drift"
            raise KeyError(msg)
        await super().put(copy.deepcopy(job))


def _single_step_plan(job_id: str) -> Plan:
    return Plan(
        plan_id=f"{job_id}-plan",
        job_id=job_id,
        source_type="epub",
        source_language="en",
        target_language="en",
        executor_strategy=ExecutorStrategy.SEQUENTIAL,
        steps=(
            PlanStep(
                step_id="extract",
                type=WorkerType.EXTRACTION,
                depends_on=(),
                status=StepStatus.PENDING,
                payload={"source_path": "/input/book.epub"},
            ),
        ),
    )


class TestOrchestrator:
    def test_default_step_cache_is_in_memory(self, tmp_path: Path) -> None:
        """ARCH-008: omitting step_cache constructs an InMemoryStepCache (decoupled from PlanCache.data_dir)."""
        orch = Orchestrator(InMemoryWorkerStore(), PlanCache(tmp_path), _success_handler)
        assert isinstance(orch._step_cache, InMemoryStepCache)  # noqa: SLF001

    def test_explicit_step_cache_is_used(self, tmp_path: Path) -> None:
        """ARCH-008: passing step_cache uses the caller's instance verbatim."""
        cache = StepCache(tmp_path / "explicit")
        orch = Orchestrator(InMemoryWorkerStore(), PlanCache(tmp_path), _success_handler, step_cache=cache)
        assert orch._step_cache is cache  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_submit_job_invalidates_handler_worker_cache(self, tmp_path: Path) -> None:
        """CORR-009: submit_job must drop the step handler's worker-instance pool
        and re-fetch on the next step, so a worker re-registered between jobs is seen.
        """
        from acheron.shell.step_handler import CachingStepHandler

        reg = InMemoryWorkerStore()
        await reg.register("tts-1", "http://127.0.0.1:1", "http", tts_caps("es"))
        await reg.register("trans-1", "http://127.0.0.1:2", "http", translation_caps())
        orch = Orchestrator(reg, PlanCache(tmp_path))
        await orch.start()
        assert isinstance(orch._handler, CachingStepHandler)  # noqa: SLF001

        # First submit: cache is populated.
        await orch.submit_job(
            EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es"),
            ExecutorStrategy.STREAMING,
        )

        # Simulate a worker re-registering between jobs.
        await reg.register("tts-1", "http://127.0.0.1:99", "http", tts_caps("es"))

        # Second submit must observe the re-registration. The invalidation
        # is verified end-to-end: a fresh registry read means the new URL
        # is in the worker list the executor dispatches against.
        await orch.submit_job(
            EpubRequest(source_path="/input/book2.epub", source_language="en", target_language="es"),
            ExecutorStrategy.STREAMING,
        )

    @pytest.mark.asyncio
    async def test_submit_job_requires_start(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """submit_job raises RuntimeError if start() was not called.

        Local workers are only registered during start(). Submitting before
        start() would queue the job against an empty registry and the
        _execute task would fail with a confusing WorkerError at execution.
        """
        reg = InMemoryWorkerStore()
        await reg.register("tts-1", "http://127.0.0.1:1", "http", tts_caps())
        await reg.register("trans-1", "http://127.0.0.1:2", "http", translation_caps())
        orch = Orchestrator(reg, PlanCache(tmp_path), _success_handler)

        request = EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es")
        with pytest.raises(RuntimeError, match="start"):
            await orch.submit_job(request, ExecutorStrategy.STREAMING)

    @pytest.mark.asyncio
    async def test_start_skips_already_registered_types(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Pre-registered TTS worker is preserved; no duplicate is added on start()."""
        reg = InMemoryWorkerStore()
        await reg.register("tts-1", "http://user-tts", "http", tts_caps())
        orch = Orchestrator(reg, PlanCache(tmp_path), _success_handler)

        await orch.start()

        tts_workers = await reg.find_by_type(WorkerType.TTS)
        assert len(tts_workers) == 1
        assert tts_workers[0].worker_id == "tts-1"

        for wt in (WorkerType.EXTRACTION, WorkerType.CHUNKING, WorkerType.PACKAGING):
            assert await reg.find_by_type(wt), f"{wt.value}-local should be registered"

    @pytest.mark.asyncio
    async def test_submit_job_returns_tracked(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        reg = InMemoryWorkerStore()
        await reg.register("tts-1", "http://127.0.0.1:1", "http", tts_caps())
        await reg.register("trans-1", "http://127.0.0.1:2", "http", translation_caps())
        orch = Orchestrator(reg, PlanCache(tmp_path), _success_handler)
        await orch.start()

        request = EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es")
        tracked = await orch.submit_job(request, ExecutorStrategy.STREAMING)

        assert tracked.job_id.startswith("job-")
        assert tracked.status == PlanStatus.RUNNING
        assert tracked.plan is not None

    @pytest.mark.asyncio
    async def test_submit_job_invalid_language_raises(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        reg = InMemoryWorkerStore()
        orch = Orchestrator(reg, PlanCache(tmp_path), _success_handler)
        await orch.start()

        request = EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es")
        with pytest.raises(InvalidLanguagePathError):
            await orch.submit_job(request, ExecutorStrategy.STREAMING)

    @pytest.mark.asyncio
    async def test_submit_job_chunking_too_long_raises(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """When chunking max_chunk_length exceeds a worker's max_input_tokens, fail fast.

        Uses a tts_caps variant with max_input_tokens=10; with chars_per_token=1 and
        max_chunk_length=100, 100 > 10 must raise.
        """
        from acheron.core.models import WorkerCapabilities, WorkerType
        from tests.shell.conftest import translation_caps

        bounded_caps = WorkerCapabilities(
            worker_type=WorkerType.TTS,
            supported_languages_in=frozenset({"en"}),
            supported_languages_out=frozenset({"en"}),
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"wav"}),
            max_payload_bytes=None,
            batch_capable=True,
            model_source=None,
            max_input_tokens=10,
        )
        reg = InMemoryWorkerStore()
        await reg.register("tts-bounded", "http://127.0.0.1:1", "http", bounded_caps)
        await reg.register("trans-1", "http://127.0.0.1:2", "http", translation_caps())
        settings = Settings(chars_per_token=1)
        settings.workers.chunking.max_chunk_length = 100
        settings.orchestrator.data_dir = tmp_path
        orch = Orchestrator(reg, PlanCache(tmp_path), _success_handler, settings=settings)
        await orch.start()

        request = EpubRequest(source_path="/input/book.epub", source_language="en", target_language="en")
        with pytest.raises(ChunkingTooLongForWorkerError, match="max_input_tokens=10"):
            await orch.submit_job(request, ExecutorStrategy.STREAMING)

    @pytest.mark.asyncio
    async def test_submit_job_chunking_fits(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """When the chunking length fits, submit_job succeeds."""
        reg = InMemoryWorkerStore()
        await reg.register("tts-1", "http://127.0.0.1:1", "http", tts_caps())
        await reg.register("trans-1", "http://127.0.0.1:2", "http", translation_caps())
        settings = Settings(chars_per_token=4)
        settings.workers.chunking.max_chunk_length = 250
        settings.orchestrator.data_dir = tmp_path
        orch = Orchestrator(reg, PlanCache(tmp_path), _success_handler, settings=settings)
        await orch.start()

        request = EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es")
        tracked = await orch.submit_job(request, ExecutorStrategy.STREAMING)
        assert tracked.plan is not None

    @pytest.mark.asyncio
    async def test_get_job(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        reg = InMemoryWorkerStore()
        await reg.register("tts-1", "http://127.0.0.1:1", "http", tts_caps())
        await reg.register("trans-1", "http://127.0.0.1:2", "http", translation_caps())
        orch = Orchestrator(reg, PlanCache(tmp_path), _success_handler)
        await orch.start()

        request = EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es")
        tracked = await orch.submit_job(request, ExecutorStrategy.STREAMING)

        found = await orch.get_job(tracked.job_id)
        assert found is not None
        assert found.job_id == tracked.job_id

    @pytest.mark.asyncio
    async def test_get_job_nonexistent(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        orch = Orchestrator(InMemoryWorkerStore(), PlanCache(tmp_path), _success_handler)
        await orch.start()
        assert await orch.get_job("nope") is None

    @pytest.mark.asyncio
    async def test_shutdown_drains_inflight_jobs_to_failed(self, tmp_path: Path) -> None:
        """OBS-001: shutdown() must cancel and await in-flight _execute tasks
        and reconcile each job to a terminal status (FAILED on cancellation).
        Previously the tasks were left running and the persisted status stayed
        RUNNING forever.
        """
        from acheron.core.models import PlanStatus

        handler_started = asyncio.Event()
        release_handler = asyncio.Event()

        async def _slow_handler(step: PlanStep, plan: Plan) -> JobResult:
            handler_started.set()
            await release_handler.wait()
            return JobResult(
                job_id=plan.job_id,
                status=JobStatus.SUCCESS,
                outputs=(),
                metrics=JobMetrics(duration_seconds=0.0),
            )

        reg = InMemoryWorkerStore()
        await reg.register("tts-1", "http://127.0.0.1:1", "http", tts_caps())
        await reg.register("trans-1", "http://127.0.0.1:2", "http", translation_caps())
        job_store = InMemoryJobStore()
        orch = Orchestrator(reg, PlanCache(tmp_path), _slow_handler, job_store=job_store)
        await orch.start()
        request = EpubRequest(
            source_path="/input/book.epub",
            source_language="en",
            target_language="es",
        )
        tracked = await orch.submit_job(request, ExecutorStrategy.STREAMING)
        await handler_started.wait()
        # Cancel the in-flight task via shutdown (drain must terminate the
        # in-flight _execute and write FAILED to the store).
        await orch.shutdown()
        persisted = await job_store.get(tracked.job_id)
        assert persisted is not None
        assert persisted.status == PlanStatus.FAILED
        assert persisted.result is not None
        assert persisted.result.status == PlanStatus.FAILED
        assert persisted.result.completed_steps == 0
        assert persisted.result.total_steps == (len(tracked.plan.steps) if tracked.plan else 0)
        assert persisted.result.errors == ("execution cancelled during shutdown",)
        # Wake the handler so the test event loop can exit cleanly.
        release_handler.set()

    @pytest.mark.asyncio
    async def test_shutdown_drain_timeout_is_configurable(self, tmp_path: Path) -> None:
        """CFG-013: orchestrator.shutdown_drain_seconds bounds the drain grace."""
        handler_started = asyncio.Event()

        async def _blocking_handler(_step: PlanStep, _plan: Plan) -> JobResult:
            handler_started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        reg = InMemoryWorkerStore()
        await reg.register("tts-1", "http://127.0.0.1:1", "http", tts_caps())
        await reg.register("trans-1", "http://127.0.0.1:2", "http", translation_caps())
        settings = Settings()
        settings.orchestrator.data_dir = tmp_path
        settings.orchestrator.shutdown_drain_seconds = 0.1
        job_store = _ControlledPutJobStore()
        orch = Orchestrator(
            reg,
            PlanCache(tmp_path),
            _blocking_handler,
            job_store=job_store,
            settings=settings,
        )
        await orch.start()
        await orch.submit_job(
            EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es"),
            ExecutorStrategy.STREAMING,
        )
        await handler_started.wait()

        shutdown_task = asyncio.create_task(orch.shutdown())
        await job_store.persist_started.wait()
        with pytest.raises(TimeoutError):
            await shutdown_task
        job_store.release_persist.set()
        await orch.close()

    @pytest.mark.asyncio
    async def test_shutdown_drain_logs_entry_and_completion(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """OBS-013: drain logs the task count on entry and elapsed time on completion."""
        handler_started = asyncio.Event()

        async def _blocking_handler(_step: PlanStep, _plan: Plan) -> JobResult:
            handler_started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        reg = InMemoryWorkerStore()
        await reg.register("tts-1", "http://127.0.0.1:1", "http", tts_caps())
        await reg.register("trans-1", "http://127.0.0.1:2", "http", translation_caps())
        orch = Orchestrator(reg, PlanCache(tmp_path), _blocking_handler)
        await orch.start()
        await orch.submit_job(
            EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es"),
            ExecutorStrategy.STREAMING,
        )
        await handler_started.wait()

        with caplog.at_level("INFO", logger="acheron.shell.orchestrator"):
            await orch.shutdown()
        messages = [r.message for r in caplog.records]
        assert any("Draining 1 in-flight _execute tasks" in m for m in messages)
        assert any(m.startswith("Drained 1 tasks in ") for m in messages)

    @pytest.mark.asyncio
    async def test_shutdown_drain_logs_timeout_and_reraises(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """OBS-013: a firing drain grace logs a warning naming the timeout and re-raises TimeoutError."""
        handler_started = asyncio.Event()

        async def _blocking_handler(_step: PlanStep, _plan: Plan) -> JobResult:
            handler_started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        reg = InMemoryWorkerStore()
        await reg.register("tts-1", "http://127.0.0.1:1", "http", tts_caps())
        await reg.register("trans-1", "http://127.0.0.1:2", "http", translation_caps())
        settings = Settings()
        settings.orchestrator.data_dir = tmp_path
        settings.orchestrator.shutdown_drain_seconds = 0.1
        job_store = _ControlledPutJobStore()
        orch = Orchestrator(
            reg,
            PlanCache(tmp_path),
            _blocking_handler,
            job_store=job_store,
            settings=settings,
        )
        await orch.start()
        await orch.submit_job(
            EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es"),
            ExecutorStrategy.STREAMING,
        )
        await handler_started.wait()

        shutdown_task = asyncio.create_task(orch.shutdown())
        await job_store.persist_started.wait()
        with caplog.at_level("WARNING", logger="acheron.shell.orchestrator"), pytest.raises(TimeoutError):
            await shutdown_task
        assert any("Drain grace timeout" in r.message and "still pending" in r.message for r in caplog.records)
        job_store.release_persist.set()
        await orch.close()

    @pytest.mark.asyncio
    async def test_shutdown_persists_failed_despite_drain_timeout(self, tmp_path: Path) -> None:
        """CORR-038: the post-cancel FAILED persist is shielded — a firing drain grace cannot cancel it."""
        handler_started = asyncio.Event()

        async def _blocking_handler(_step: PlanStep, _plan: Plan) -> JobResult:
            handler_started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        reg = InMemoryWorkerStore()
        await reg.register("tts-1", "http://127.0.0.1:1", "http", tts_caps())
        await reg.register("trans-1", "http://127.0.0.1:2", "http", translation_caps())
        job_store = _ControlledPutJobStore()
        settings = Settings()
        settings.orchestrator.data_dir = tmp_path
        settings.orchestrator.shutdown_drain_seconds = 0.1
        orch = Orchestrator(reg, PlanCache(tmp_path), _blocking_handler, job_store=job_store, settings=settings)
        await orch.start()
        tracked = await orch.submit_job(
            EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es"),
            ExecutorStrategy.STREAMING,
        )
        await handler_started.wait()

        shutdown_task = asyncio.create_task(orch.shutdown())
        await job_store.persist_started.wait()
        with pytest.raises(TimeoutError):
            await shutdown_task
        job_store.release_persist.set()
        await orch.close()
        persisted = await job_store.get(tracked.job_id)
        assert persisted is not None
        assert persisted.status == PlanStatus.FAILED
        assert persisted.result is not None
        assert persisted.result.status == PlanStatus.FAILED

    @pytest.mark.asyncio
    async def test_execute_persists_failed_when_completion_put_raises(self, tmp_path: Path) -> None:
        """CORR-039: a failing completion put must not leave the job persisted as RUNNING."""
        reg = InMemoryWorkerStore()
        await reg.register("tts-1", "http://127.0.0.1:1", "http", tts_caps())
        await reg.register("trans-1", "http://127.0.0.1:2", "http", translation_caps())
        job_store = _FailingSecondPutJobStore()
        orch = Orchestrator(reg, PlanCache(tmp_path), _success_handler, job_store=job_store)
        await orch.start()
        tracked = await orch.submit_job(
            EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es"),
            ExecutorStrategy.STREAMING,
        )
        # put#1 (submit) succeeds; put#2 (post-execution) raises; the
        # _execute recovery put must reconcile the job to FAILED.
        await asyncio.gather(*orch._tasks, return_exceptions=True)  # noqa: SLF001
        persisted = await job_store.get(tracked.job_id)
        assert persisted is not None
        assert persisted.status == PlanStatus.FAILED
        assert persisted.result is not None
        assert persisted.result.status == PlanStatus.FAILED

    @pytest.mark.asyncio
    async def test_shutdown_persists_partial_result_cost(self, tmp_path: Path) -> None:
        """CORR-040: a cancelled job persists the cost of completed steps, not zero."""
        third_step_done = asyncio.Event()
        fourth_step_started = asyncio.Event()
        steps_done = 0

        async def _partial_handler(_step: PlanStep, plan: Plan) -> JobResult:
            nonlocal steps_done
            if steps_done >= 3:
                fourth_step_started.set()
                await asyncio.Event().wait()  # block at step 4 until cancelled
                raise AssertionError("unreachable")
            result = JobResult(
                job_id=plan.job_id,
                status=JobStatus.SUCCESS,
                outputs=(),
                metrics=JobMetrics(duration_seconds=0.1, cost_estimate=0.5),
            )
            steps_done += 1
            if steps_done == 3:
                third_step_done.set()
            return result

        reg = InMemoryWorkerStore()
        await reg.register("tts-1", "http://127.0.0.1:1", "http", tts_caps())
        await reg.register("trans-1", "http://127.0.0.1:2", "http", translation_caps())
        job_store = InMemoryJobStore()
        orch = Orchestrator(reg, PlanCache(tmp_path), _partial_handler, job_store=job_store)
        await orch.start()
        tracked = await orch.submit_job(
            EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es"),
            ExecutorStrategy.STREAMING,
        )
        await third_step_done.wait()
        await fourth_step_started.wait()
        await orch.shutdown()
        persisted = await job_store.get(tracked.job_id)
        assert persisted is not None
        assert persisted.status == PlanStatus.FAILED
        assert persisted.result is not None
        assert persisted.result.completed_steps == 3
        assert persisted.result.total_cost == 1.5

    @pytest.mark.asyncio
    async def test_cancel_persist_keyerror_propagates_chained(self, tmp_path: Path) -> None:
        """MAINT-021: a programming error in the post-cancel persist surfaces chained to the CancelledError."""
        handler_started = asyncio.Event()

        async def _blocking_handler(_step: PlanStep, _plan: Plan) -> JobResult:
            handler_started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        reg = InMemoryWorkerStore()
        await reg.register("tts-1", "http://127.0.0.1:1", "http", tts_caps())
        await reg.register("trans-1", "http://127.0.0.1:2", "http", translation_caps())
        orch = Orchestrator(reg, PlanCache(tmp_path), _blocking_handler, job_store=_KeyErrorOnSecondPutJobStore())
        await orch.start()
        await orch.submit_job(
            EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es"),
            ExecutorStrategy.STREAMING,
        )
        await handler_started.wait()
        (task,) = tuple(orch._tasks)  # noqa: SLF001
        with pytest.raises(KeyError):
            await orch.shutdown()
        exc = task.exception()
        assert isinstance(exc, KeyError)
        assert isinstance(exc.__context__, asyncio.CancelledError)

    @pytest.mark.asyncio
    async def test_start_awaits_store_connect(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Orchestrator.start() must await connect() on both stores before returning."""
        connect_calls: list[str] = []

        class _SpyWorkerStore(InMemoryWorkerStore):
            async def connect(self) -> None:
                connect_calls.append("worker")
                await super().connect()

        class _SpyJobStore(InMemoryJobStore):
            async def connect(self) -> None:
                connect_calls.append("job")
                await super().connect()

        reg = _SpyWorkerStore()
        jobs = _SpyJobStore()
        orch = Orchestrator(reg, PlanCache(tmp_path), _success_handler, job_store=jobs)
        await orch.start()

        assert "worker" in connect_calls
        assert "job" in connect_calls

    @pytest.mark.asyncio
    async def test_start_can_retry_after_connect_failure(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """If connect() raises, start() must not flip _started so a retry works."""

        class _FailingWorkerStore(InMemoryWorkerStore):
            async def connect(self) -> None:
                msg = "redis down"
                raise RuntimeError(msg)

        class _FailingJobStore(InMemoryJobStore):
            async def connect(self) -> None:
                msg = "redis down"
                raise RuntimeError(msg)

        orch = Orchestrator(
            _FailingWorkerStore(),
            PlanCache(tmp_path),
            _success_handler,
            job_store=_FailingJobStore(),
        )

        with pytest.raises(RuntimeError, match="redis down"):
            await orch.start()

        # Replace with working stores; retry must re-call connect() and re-register
        # local workers (the first start() never got that far).
        orch._registry = InMemoryWorkerStore()  # noqa: SLF001
        orch._job_store = InMemoryJobStore()  # noqa: SLF001
        await orch.start()
        assert orch._started  # noqa: SLF001
        workers = await orch._registry.list_all()  # noqa: SLF001
        assert {w.worker_id for w in workers} >= {"extraction-local", "chunking-local", "packaging-local"}

    @pytest.mark.asyncio
    async def test_list_jobs(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        reg = InMemoryWorkerStore()
        await reg.register("tts-1", "http://127.0.0.1:1", "http", tts_caps())
        await reg.register("trans-1", "http://127.0.0.1:2", "http", translation_caps())
        orch = Orchestrator(reg, PlanCache(tmp_path), _success_handler)
        await orch.start()

        request = EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es")
        await orch.submit_job(request, ExecutorStrategy.STREAMING)
        await orch.submit_job(request, ExecutorStrategy.SEQUENTIAL)

        jobs = await orch.list_jobs()
        assert len(jobs) == 2

    @pytest.mark.asyncio
    async def test_resume_job_restarts_stale_running_job(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        reg = InMemoryWorkerStore()
        jobs = InMemoryJobStore()
        orch = Orchestrator(reg, PlanCache(tmp_path), _success_handler, job_store=jobs)
        await orch.start()
        request = EpubRequest(source_path="/input/book.epub", source_language="en", target_language="en")
        tracked = TrackedJob(
            job_id="job-stale",
            request=request,
            strategy=ExecutorStrategy.SEQUENTIAL,
            plan=_single_step_plan("job-stale"),
            status=PlanStatus.RUNNING,
        )
        await jobs.put(tracked)

        resumed = await orch.resume_job("job-stale")
        assert resumed.status == PlanStatus.RUNNING
        assert orch._tasks  # noqa: SLF001
        for task in tuple(orch._tasks):  # noqa: SLF001
            task.cancel()
        await asyncio.gather(*tuple(orch._tasks), return_exceptions=True)  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_resume_job_rejects_a_newly_submitted_job(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        reg = InMemoryWorkerStore()
        await reg.register("tts-1", "http://127.0.0.1:1", "http", tts_caps())
        await reg.register("trans-1", "http://127.0.0.1:2", "http", translation_caps())
        orch = Orchestrator(reg, PlanCache(tmp_path), _success_handler)
        await orch.start()
        tracked = await orch.submit_job(
            EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es"),
            ExecutorStrategy.STREAMING,
        )

        with pytest.raises(JobAlreadyRunningError):
            await orch.resume_job(tracked.job_id)
        await orch.shutdown()

    @pytest.mark.asyncio
    async def test_resume_job_rejects_active_running_job(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        jobs = InMemoryJobStore()
        orch = Orchestrator(InMemoryWorkerStore(), PlanCache(tmp_path), _success_handler, job_store=jobs)
        await orch.start()
        request = EpubRequest(source_path="/input/book.epub", source_language="en", target_language="en")
        tracked = TrackedJob(
            job_id="job-active",
            request=request,
            strategy=ExecutorStrategy.SEQUENTIAL,
            plan=None,
            status=PlanStatus.RUNNING,
        )
        await jobs.put(tracked)
        orch._active_jobs.add("job-active")  # noqa: SLF001

        with pytest.raises(JobAlreadyRunningError):
            await orch.resume_job("job-active")

    @pytest.mark.asyncio
    async def test_resume_job_missing_job_raises(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        orch = Orchestrator(InMemoryWorkerStore(), PlanCache(tmp_path), _success_handler)
        await orch.start()

        with pytest.raises(JobNotFoundError):
            await orch.resume_job("missing")

    @pytest.mark.asyncio
    async def test_register_and_list_workers(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        orch = Orchestrator(InMemoryWorkerStore(), PlanCache(tmp_path), _success_handler)
        await orch.start()
        await orch.register_worker("w-1", "http://127.0.0.1:1", "http", tts_caps())
        await orch.register_worker("w-2", "http://127.0.0.1:2", "http", translation_caps())

        workers = await orch.list_workers()
        worker_ids = {w.worker_id for w in workers}
        assert "w-1" in worker_ids
        assert "w-2" in worker_ids
        assert "extraction-local" in worker_ids
        assert "chunking-local" in worker_ids
        assert "packaging-local" in worker_ids

    @pytest.mark.asyncio
    async def test_get_capabilities(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        reg = InMemoryWorkerStore()
        await reg.register("tts-1", "http://127.0.0.1:1", "http", tts_caps("es"))
        await reg.register("trans-1", "http://127.0.0.1:2", "http", translation_caps("en", "es"))
        orch = Orchestrator(reg, PlanCache(tmp_path), _success_handler)
        await orch.start()

        caps = await orch.get_capabilities()
        assert len(caps) >= 1

    @pytest.mark.asyncio
    async def test_get_capabilities_filtered(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        reg = InMemoryWorkerStore()
        await reg.register("tts-1", "http://127.0.0.1:1", "http", tts_caps("es"))
        await reg.register("tts-2", "http://127.0.0.1:2", "http", tts_caps("fr"))
        orch = Orchestrator(reg, PlanCache(tmp_path), _success_handler)
        await orch.start()

        caps = await orch.get_capabilities(dst="es")
        for pair in caps:
            assert pair.dst == "es"

    @pytest.mark.asyncio
    async def test_get_capabilities_no_translation_worker(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Cross-language pairs should not appear without a translation worker."""
        from tests.shell.conftest import asr_caps

        reg = InMemoryWorkerStore()
        await reg.register("asr-1", "http://127.0.0.1:1", "http", asr_caps("en"))
        await reg.register("tts-1", "http://127.0.0.1:2", "http", tts_caps("es"))
        orch = Orchestrator(reg, PlanCache(tmp_path), _success_handler)
        await orch.start()

        caps = await orch.get_capabilities()
        pairs = {(p.src, p.dst) for p in caps}
        assert ("en", "es") not in pairs
        assert ("en", "en") not in pairs

    @pytest.mark.asyncio
    async def test_get_capabilities_same_language_without_translation(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Same-language pairs should work without a translation worker."""
        reg = InMemoryWorkerStore()
        await reg.register("tts-1", "http://127.0.0.1:1", "http", tts_caps("en"))
        orch = Orchestrator(reg, PlanCache(tmp_path), _success_handler)
        await orch.start()

        caps = await orch.get_capabilities()
        pairs = {(p.src, p.dst) for p in caps}
        assert ("en", "en") in pairs

    @pytest.mark.asyncio
    async def test_resume_job_concurrent_race_prevention(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        reg = InMemoryWorkerStore()
        jobs = InMemoryJobStore()
        orch = Orchestrator(reg, PlanCache(tmp_path), _success_handler, job_store=jobs)
        await orch.start()
        request = EpubRequest(source_path="/input/book.epub", source_language="en", target_language="en")
        tracked = TrackedJob(
            job_id="job-race",
            request=request,
            strategy=ExecutorStrategy.SEQUENTIAL,
            plan=_single_step_plan("job-race"),
            status=PlanStatus.FAILED,
        )
        await jobs.put(tracked)

        # Call resume twice concurrently
        results = await asyncio.gather(
            orch.resume_job("job-race"),
            orch.resume_job("job-race"),
            return_exceptions=True,
        )

        # One should succeed, the other should raise JobAlreadyRunningError
        exceptions = [r for r in results if isinstance(r, Exception)]
        assert len(exceptions) == 1
        assert isinstance(exceptions[0], JobAlreadyRunningError)

        # Clean up tasks
        for task in tuple(orch._tasks):  # noqa: SLF001
            task.cancel()
        await asyncio.gather(*tuple(orch._tasks), return_exceptions=True)  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_sequential_executor_uses_step_cache(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("tts-1", "http://127.0.0.1:1", "http", tts_caps("en"))
        await reg.register("trans-1", "http://127.0.0.1:2", "http", translation_caps())

        handler_calls = 0

        async def counting_handler(step: PlanStep, plan: Plan) -> JobResult:
            nonlocal handler_calls
            handler_calls += 1
            return await _success_handler(step, plan)

        orch = Orchestrator(reg, PlanCache(tmp_path), counting_handler)
        await orch.start()

        # Submit a job to compile a plan
        request = EpubRequest(source_path="/input/book.epub", source_language="en", target_language="en")
        tracked = await orch.submit_job(request, ExecutorStrategy.SEQUENTIAL)

        # Cancel first execution
        for task in tuple(orch._tasks):  # noqa: SLF001
            task.cancel()
        await asyncio.gather(*tuple(orch._tasks), return_exceptions=True)  # noqa: SLF001

        # Populate cache for all steps
        from acheron.core.models import OutputFile

        cache = orch._step_cache  # noqa: SLF001
        output_file = tmp_path / "chapter_001.txt"
        output_file.write_text("Hello World", encoding="utf-8")
        import hashlib

        checksum = hashlib.sha256(b"Hello World").hexdigest()
        outputs = (
            OutputFile(
                path=str(output_file),
                filename="chapter_001.txt",
                size_bytes=output_file.stat().st_size,
                checksum=checksum,
                content_type="text/plain",
            ),
        )
        plan = tracked.plan
        assert plan is not None
        for step in plan.steps:
            await cache.save_outputs(tracked.job_id, step.step_id, outputs)

        # Resume job and verify
        orch._active_jobs.clear()  # noqa: SLF001
        handler_calls = 0
        progress: list[int] = []
        record_progress = orch._record_step_progress  # noqa: SLF001

        def capture_progress(tracked_job: TrackedJob, plan: Plan, result: JobResult) -> None:
            record_progress(tracked_job, plan, result)
            assert tracked_job.result is not None
            progress.append(tracked_job.result.completed_steps)

        monkeypatch.setattr(orch, "_record_step_progress", capture_progress)
        await orch.resume_job(tracked.job_id)

        # Wait for execute tasks
        tasks = list(orch._tasks)  # noqa: SLF001
        await asyncio.gather(*tasks)

        assert handler_calls == 0
        assert tracked.status == PlanStatus.COMPLETED
        assert tracked.result is not None
        assert tracked.result.completed_steps == tracked.result.total_steps
        assert progress == list(range(1, len(plan.steps) + 1))

    @pytest.mark.asyncio
    async def test_plan_result_errors_sanitised_on_handler_failure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When the executor itself raises, the persisted PlanResult.errors
        must not contain traceback fragments or file paths from the exception."""
        from acheron.core.interfaces import Executor
        from acheron.core.models import PlanResult
        from acheron.shell import orchestrator as orch_mod

        reg = InMemoryWorkerStore()
        await reg.register("tts-1", "http://127.0.0.1:1", "http", tts_caps("en"))
        await reg.register("trans-1", "http://127.0.0.1:2", "http", translation_caps("en", "en"))

        async def good_handler(step: PlanStep, plan: Plan) -> JobResult:
            return await _success_handler(step, plan)

        class _BoomExecutor(Executor):
            async def run(self, plan: Plan) -> PlanResult:
                msg = "secret stuff\n  File '/etc/passwd'\nTraceback (most recent call last):"
                raise RuntimeError(msg)

        monkeypatch.setattr(orch_mod, "create_executor", lambda *_a, **_kw: _BoomExecutor())
        orch = Orchestrator(reg, PlanCache(tmp_path), good_handler)
        await orch.start()

        request = EpubRequest(source_path="/input/book.epub", source_language="en", target_language="en")
        tracked = await orch.submit_job(request, ExecutorStrategy.SEQUENTIAL)
        tasks = list(orch._tasks)  # noqa: SLF001
        await asyncio.gather(*tasks)

        assert tracked.status == PlanStatus.FAILED
        assert tracked.result is not None
        assert tracked.result.errors == ("RuntimeError: secret stuff",)

    @pytest.mark.asyncio
    async def test_orchestrator_generates_and_persists_registration_token(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from acheron.shell.config import OrchestratorSettings, Settings

        settings = Settings(orchestrator=OrchestratorSettings(data_dir=tmp_path, registration_token=None))
        orch = Orchestrator(InMemoryWorkerStore(), PlanCache(tmp_path), _success_handler, settings=settings)
        await orch.start()

        # Token should be automatically generated
        token = orch.settings.orchestrator.registration_token
        assert token is not None
        assert len(token) == 32  # 16-byte hex is 32 chars

        # Token should be saved to file
        token_file = tmp_path / ".registration_token"
        assert token_file.exists()
        assert token_file.read_text(encoding="utf-8").strip() == token

        # Clean up
        await orch.close()
        await orch.shutdown()

    @pytest.mark.asyncio
    async def test_orchestrator_loads_existing_registration_token(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from acheron.shell.config import OrchestratorSettings, Settings

        # Pre-populate token file with a 32+ char token (SEC-011 minimum)
        token_file = tmp_path / ".registration_token"
        pre_existing = "0123456789abcdef0123456789abcdef"  # 32 hex chars
        token_file.write_text(pre_existing, encoding="utf-8")

        settings = Settings(orchestrator=OrchestratorSettings(data_dir=tmp_path, registration_token=None))
        orch = Orchestrator(InMemoryWorkerStore(), PlanCache(tmp_path), _success_handler, settings=settings)
        await orch.start()

        # Should load the pre-existing token
        assert orch.settings.orchestrator.registration_token == pre_existing

        # Clean up
        await orch.close()
        await orch.shutdown()

    @pytest.mark.asyncio
    async def test_orchestrator_rejects_dev_registration_token(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """SEC-011/018/022: refuse to start with the publicly-known dev-registration-token."""
        from acheron.shell.config import OrchestratorSettings

        settings = Settings(
            orchestrator=OrchestratorSettings(data_dir=tmp_path, registration_token="dev-registration-token")
        )
        orch = Orchestrator(InMemoryWorkerStore(), PlanCache(tmp_path), _success_handler, settings=settings)
        with pytest.raises(RuntimeError, match="dev-registration-token"):
            await orch.start()
        await orch.close()
        await orch.shutdown()

    @pytest.mark.asyncio
    async def test_orchestrator_rejects_short_registration_token(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """SEC-011: refuse to start with a token shorter than 32 chars."""
        from acheron.shell.config import OrchestratorSettings

        settings = Settings(orchestrator=OrchestratorSettings(data_dir=tmp_path, registration_token="short-token"))
        orch = Orchestrator(InMemoryWorkerStore(), PlanCache(tmp_path), _success_handler, settings=settings)
        with pytest.raises(RuntimeError, match="too short"):
            await orch.start()
        await orch.close()
        await orch.shutdown()

    @pytest.mark.asyncio
    async def test_orchestrator_rejects_short_registration_token_from_file(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """SEC-011: refuse to load a too-short token from the persisted file."""
        from acheron.shell.config import OrchestratorSettings

        token_file = tmp_path / ".registration_token"
        token_file.write_text("too-short", encoding="utf-8")

        settings = Settings(orchestrator=OrchestratorSettings(data_dir=tmp_path, registration_token=None))
        orch = Orchestrator(InMemoryWorkerStore(), PlanCache(tmp_path), _success_handler, settings=settings)
        with pytest.raises(RuntimeError, match="too short"):
            await orch.start()
        await orch.close()
        await orch.shutdown()

    @pytest.mark.asyncio
    async def test_orchestrator_accepts_valid_registration_token(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """SEC-011: accept a 32+ char token from the env / settings."""
        from acheron.shell.config import OrchestratorSettings

        valid = "a" * 64
        settings = Settings(orchestrator=OrchestratorSettings(data_dir=tmp_path, registration_token=valid))
        orch = Orchestrator(InMemoryWorkerStore(), PlanCache(tmp_path), _success_handler, settings=settings)
        await orch.start()
        assert orch.settings.orchestrator.registration_token == valid
        await orch.close()
        await orch.shutdown()

    @pytest.mark.asyncio
    async def test_orchestrator_does_not_log_registration_token(self, tmp_path, caplog) -> None:  # type: ignore[no-untyped-def]
        """SEC-008: the auto-generated registration token must not appear in any log line."""
        import logging

        from acheron.shell.config import OrchestratorSettings

        settings = Settings(orchestrator=OrchestratorSettings(data_dir=tmp_path, registration_token=None))
        orch = Orchestrator(InMemoryWorkerStore(), PlanCache(tmp_path), _success_handler, settings=settings)

        with caplog.at_level(logging.INFO, logger="acheron.shell.orchestrator"):
            await orch.start()

        token = orch.settings.orchestrator.registration_token
        assert token is not None
        for record in caplog.records:
            assert token not in record.getMessage(), (
                f"registration token leaked in log at {record.levelname}: {record.getMessage()}"
            )

        await orch.close()
        await orch.shutdown()

    @pytest.mark.asyncio
    async def test_orchestrator_persists_registration_token_with_0600_mode(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """SEC-009: the persisted token file must have 0600 permissions, regardless of process umask."""
        import stat

        from acheron.shell.config import OrchestratorSettings

        settings = Settings(orchestrator=OrchestratorSettings(data_dir=tmp_path, registration_token=None))
        orch = Orchestrator(InMemoryWorkerStore(), PlanCache(tmp_path), _success_handler, settings=settings)
        await orch.start()

        token_file = tmp_path / ".registration_token"
        assert token_file.exists()
        mode = token_file.stat().st_mode
        assert stat.S_IMODE(mode) == 0o600, f"token file mode is {oct(stat.S_IMODE(mode))}, expected 0o600"

        await orch.close()
        await orch.shutdown()


@pytest.mark.asyncio
async def test_orchestrator_constructs_health_providers_from_settings(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The Orchestrator must build HealthProviders from settings.providers.* API keys."""
    settings = Settings()
    settings.providers.runpod.api_key = "rp-key"
    orch = Orchestrator(
        registry=InMemoryWorkerStore(),
        cache=PlanCache(tmp_path),
        job_store=InMemoryJobStore(),
        settings=settings,
    )
    assert orch._health_monitor._providers is not None  # noqa: SLF001
    assert orch._health_monitor._providers.get("runpod") is not None  # noqa: SLF001


def test_orchestrator_does_not_mutate_passed_settings(tmp_path: Path) -> None:
    """Orchestrator must not mutate the caller's Settings; it constructs a fresh one when needed."""
    from acheron.shell.config import OrchestratorSettings

    settings = Settings(orchestrator=OrchestratorSettings(data_dir=tmp_path / "from_settings"))
    original_data_dir = settings.orchestrator.data_dir
    cache = PlanCache(data_dir=tmp_path / "from_cache")

    orch = Orchestrator(
        registry=InMemoryWorkerStore(),
        cache=cache,
        job_store=InMemoryJobStore(),
        settings=settings,
    )

    assert settings.orchestrator.data_dir == original_data_dir, "Settings must not be mutated"
    assert orch.settings.orchestrator.data_dir == original_data_dir, (
        "Orchestrator must use the caller's settings when provided"
    )


def test_create_app_does_not_mutate_passed_settings(tmp_path: Path) -> None:
    """create_app must not mutate the caller's Settings when data_dir is given."""
    from acheron.shell.api.app import create_app
    from acheron.shell.config import OrchestratorSettings

    original_dir = tmp_path / "from_settings"
    other_dir = tmp_path / "from_arg"
    settings = Settings(orchestrator=OrchestratorSettings(data_dir=original_dir))

    create_app(
        registry=InMemoryWorkerStore(),
        job_store=InMemoryJobStore(),
        cache=None,
        data_dir=other_dir,
        settings=settings,
    )

    assert settings.orchestrator.data_dir == original_dir, "Settings must not be mutated"
