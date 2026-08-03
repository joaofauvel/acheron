"""Tests for the orchestrator."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import hashlib
from collections.abc import Collection
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from acheron.core.errors import (
    ChunkingTooLongForWorkerError,
    InvalidationTargetError,
    InvalidLanguagePathError,
    JobAlreadyRunningError,
    JobNotCancellableError,
    JobNotFoundError,
    JobNotResumableError,
    NoPlanToResumeError,
    VoiceSelectionError,
)
from acheron.core.models import (
    AudioRequest,
    CostBasis,
    CostBreakdown,
    CostEstimate,
    EpubRequest,
    ExecutorStrategy,
    JobMetrics,
    JobResult,
    JobStatus,
    OutputFile,
    Plan,
    PlanResult,
    PlanStatus,
    PlanStep,
    StepStatus,
    WorkerType,
)
from acheron.core.schemas import JobLogEvent
from acheron.shell.cache import InMemoryStepCache, PlanCache, StepCache
from acheron.shell.config import Settings
from acheron.shell.job_events import iter_events
from acheron.shell.job_store import TrackedJob
from acheron.shell.orchestrator import Orchestrator
from acheron.shell.stores.base import StoreError
from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore
from acheron.shell.transports.http import HttpWorker
from tests.shell.conftest import asr_caps, translation_caps, tts_caps


async def _success_handler(_step: PlanStep, _plan: Plan) -> JobResult:
    return JobResult(
        job_id="noop",
        status=JobStatus.SUCCESS,
        outputs=(),
        metrics=JobMetrics(duration_seconds=0.01),
    )


class _ControlledPutJobStore(InMemoryJobStore):
    """Job store that gates the first post-dispatch reconciliation write."""

    def __init__(self) -> None:
        super().__init__()
        self.persist_started = asyncio.Event()
        self.persist_cancelled = asyncio.Event()
        self.release_persist = asyncio.Event()
        self._puts = 0

    async def put(self, job: TrackedJob) -> None:
        self._puts += 1
        if self._puts == 3:
            self.persist_started.set()
            try:
                await self.release_persist.wait()
            except asyncio.CancelledError:
                self.persist_cancelled.set()
                raise
        await super().put(copy.deepcopy(job))


class _FailingReconciliationPutJobStore(InMemoryJobStore):
    """Fails the terminal completion persistence write."""

    async def put(self, job: TrackedJob) -> None:
        if job.status is PlanStatus.COMPLETED:
            msg = "store temporarily unavailable"
            raise RuntimeError(msg)
        await super().put(copy.deepcopy(job))


class _KeyErrorOnReconciliationPutJobStore(InMemoryJobStore):
    """Raises KeyError on cancellation reconciliation persistence."""

    async def put(self, job: TrackedJob) -> None:
        if job.result is not None and job.result.status is PlanStatus.FAILED:
            msg = "serialiser drift"
            raise KeyError(msg)
        await super().put(copy.deepcopy(job))


class _DelayedCancellationPutJobStore(InMemoryJobStore):
    """Delays the operator-cancellation write until released by the test."""

    def __init__(self) -> None:
        super().__init__()
        self.persist_started = asyncio.Event()
        self.release_persist = asyncio.Event()

    async def put(self, job: TrackedJob) -> None:
        if job.result is not None and any(error.message == "cancelled by operator" for error in job.result.errors):
            self.persist_started.set()
            await self.release_persist.wait()
        await super().put(copy.deepcopy(job))


class _DelayedBackgroundPutJobStore(InMemoryJobStore):
    """Delays a pre-existing running-state background write."""

    def __init__(self) -> None:
        super().__init__()
        self.persist_started = asyncio.Event()
        self.release_persist = asyncio.Event()
        self.delay_enabled = False
        self._delayed = False
        self.snapshots: list[TrackedJob] = []

    async def put(self, job: TrackedJob) -> None:
        snapshot = copy.deepcopy(job)
        self.snapshots.append(copy.deepcopy(snapshot))
        if self.delay_enabled and not self._delayed:
            self._delayed = True
            self.persist_started.set()
            await self.release_persist.wait()
        await super().put(snapshot)


class _ExecutionStartupRaceJobStore(InMemoryJobStore):
    """Pauses execution's initial read to make the startup race deterministic."""

    def __init__(self) -> None:
        super().__init__()
        self.initial_get_started = asyncio.Event()
        self.release_initial_get = asyncio.Event()
        self.reaper_get_started = asyncio.Event()
        self._get_count = 0

    async def get(self, job_id: str) -> TrackedJob | None:
        self._get_count += 1
        if self._get_count == 1:
            self.initial_get_started.set()
            await self.release_initial_get.wait()
        else:
            self.reaper_get_started.set()
        return await super().get(job_id)


class _StoreErrorOnReconciliationPutJobStore(InMemoryJobStore):
    """Raises a domain persistence error during cancellation reconciliation."""

    def __init__(self) -> None:
        super().__init__()
        self._puts = 0

    async def put(self, job: TrackedJob) -> None:
        self._puts += 1
        if self._puts == 3:
            raise StoreError("store temporarily unavailable")
        await super().put(copy.deepcopy(job))


class _ObservingJobStore(InMemoryJobStore):
    """Retains snapshots so tests can inspect persisted progress transitions."""

    def __init__(self) -> None:
        super().__init__()
        self.snapshots: list[TrackedJob] = []

    async def put(self, job: TrackedJob) -> None:
        self.snapshots.append(copy.deepcopy(job))
        await super().put(copy.deepcopy(job))


class _RecordingStepCache(InMemoryStepCache):
    """Records invalidation requests for resume tests."""

    def __init__(self, data_dir: Path) -> None:
        super().__init__(data_dir)
        self.invalidated: list[set[str]] = []

    async def invalidate_steps(self, job_id: str, step_ids: Collection[str]) -> None:
        self.invalidated.append(set(step_ids))
        await super().invalidate_steps(job_id, step_ids)


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
    @pytest.mark.asyncio
    async def test_execution_startup_race_is_serialized_with_reaping(self, tmp_path: Path) -> None:
        jobs = _ExecutionStartupRaceJobStore()
        orch = Orchestrator(InMemoryWorkerStore(), PlanCache(tmp_path), _success_handler, job_store=jobs)
        now = datetime(2026, 7, 30, tzinfo=UTC)
        tracked = TrackedJob(
            job_id="job-race",
            request=EpubRequest("/input/book.epub", "en", "es"),
            strategy=ExecutorStrategy.SEQUENTIAL,
            plan=_single_step_plan("job-race"),
            status=PlanStatus.RUNNING,
            created_at=now - timedelta(minutes=5),
            last_persisted_at=now - timedelta(seconds=61),
        )
        await jobs.put(tracked)
        jobs._jobs[tracked.job_id].last_persisted_at = now - timedelta(seconds=61)  # noqa: SLF001

        execution = asyncio.create_task(orch._execute(tracked))  # noqa: SLF001
        await jobs.initial_get_started.wait()
        reaping = asyncio.create_task(
            orch.reap_stale_jobs(older_than_seconds=60, reason="orphaned_by_restart", now=now)
        )
        await asyncio.sleep(0.01)
        assert not jobs.reaper_get_started.is_set()

        jobs.release_initial_get.set()
        reaped = await reaping
        await execution

        assert reaped.job_ids == ()
        persisted = await jobs.get("job-race")
        assert persisted is not None
        assert persisted.status is PlanStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_reap_stale_jobs_excludes_active_and_terminal(self, tmp_path: Path) -> None:
        jobs = InMemoryJobStore()
        orch = Orchestrator(InMemoryWorkerStore(), PlanCache(tmp_path), _success_handler, job_store=jobs)
        now = datetime(2026, 7, 30, tzinfo=UTC)
        for job_id, status in (
            ("job-orphaned", PlanStatus.RUNNING),
            ("job-active", PlanStatus.RUNNING),
            ("job-terminal", PlanStatus.COMPLETED),
        ):
            await jobs.put(
                TrackedJob(
                    job_id=job_id,
                    request=EpubRequest("/input/book.epub", "en", "es"),
                    strategy=ExecutorStrategy.SEQUENTIAL,
                    status=status,
                    created_at=now - timedelta(minutes=5),
                    last_persisted_at=now - timedelta(minutes=5),
                )
            )
            jobs._jobs[job_id].last_persisted_at = now - timedelta(seconds=61)  # noqa: SLF001
        orch._active_jobs.add("job-active")  # noqa: SLF001

        reaped = await orch.reap_stale_jobs(older_than_seconds=60, reason=" orphaned_by_restart\nsecret", now=now)

        assert reaped.job_ids == ("job-orphaned",)
        orphan = await jobs.get("job-orphaned")
        assert orphan is not None
        assert orphan.status is PlanStatus.FAILED
        assert orphan.result is not None
        assert orphan.result.errors[-1].message == "orphaned_by_restart"
        active = await jobs.get("job-active")
        terminal = await jobs.get("job-terminal")
        assert active is not None
        assert active.status is PlanStatus.RUNNING
        assert terminal is not None
        assert terminal.status is PlanStatus.COMPLETED

    def test_default_step_cache_uses_canonical_data_dir(self, tmp_path: Path) -> None:
        orch = Orchestrator(InMemoryWorkerStore(), PlanCache(tmp_path), _success_handler)
        assert isinstance(orch._step_cache, StepCache)  # noqa: SLF001
        assert orch._step_cache.data_dir == tmp_path.resolve()  # noqa: SLF001

    def test_explicit_step_cache_is_used(self, tmp_path: Path) -> None:
        """ARCH-008: passing step_cache uses the caller's instance verbatim."""
        cache = StepCache(tmp_path)
        orch = Orchestrator(InMemoryWorkerStore(), PlanCache(tmp_path), _success_handler, step_cache=cache)
        assert orch._step_cache is cache  # noqa: SLF001

    def test_explicit_step_cache_root_mismatch_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="canonical orchestrator data directory"):
            Orchestrator(
                InMemoryWorkerStore(),
                PlanCache(tmp_path),
                _success_handler,
                step_cache=StepCache(tmp_path / "other"),
            )

    @pytest.mark.asyncio
    async def test_default_handler_reads_upstream_manifest_from_orchestrator_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        registry = InMemoryWorkerStore()
        await registry.register("tts-remote", "http://worker:8000", "http", tts_caps("es"))
        cache = StepCache(tmp_path)
        jobs = InMemoryJobStore()
        from acheron.shell.step_handler import CachingStepHandler, create_step_handler

        injected_handler = create_step_handler(registry, data_dir=tmp_path)
        orch = Orchestrator(
            registry,
            PlanCache(tmp_path),
            handler=injected_handler,
            job_store=jobs,
            step_cache=cache,
        )
        assert isinstance(injected_handler, CachingStepHandler)
        assert injected_handler._step_cache is cache  # noqa: SLF001
        chunks = b"orchestrator-owned chunks"
        chunks_path = tmp_path / "chunks.json"
        chunks_path.write_bytes(chunks)
        await cache.save_outputs(
            "job-shared",
            "chunk",
            (
                OutputFile(
                    path=str(chunks_path),
                    filename=chunks_path.name,
                    size_bytes=len(chunks),
                    checksum="x" * 64,
                    content_type="application/json",
                ),
            ),
        )
        seen: dict[str, bytes] = {}

        async def _request(_worker: HttpWorker, _method: str, _path: str, **kwargs: Any) -> httpx.Response:
            content = kwargs["content"]
            seen["body"] = b"".join([part async for part in content])
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content=b'{"job_id":"job-shared-synthesize","status":"success","outputs":[],'
                b'"metrics":{"duration_seconds":0.0},"error":null}',
            )

        monkeypatch.setattr(HttpWorker, "_request", _request)
        plan = Plan(
            plan_id="plan-shared",
            job_id="job-shared",
            source_type="epub",
            source_language="en",
            target_language="es",
            executor_strategy=ExecutorStrategy.STREAMING,
            steps=(
                PlanStep(
                    step_id="synthesize",
                    type=WorkerType.TTS,
                    depends_on=("chunk",),
                    status=StepStatus.PENDING,
                    payload={"chapter_id": "ch1"},
                    selected_worker_id="tts-remote",
                ),
            ),
        )

        result = await orch._handler(plan.steps[0], plan)  # noqa: SLF001

        assert result.status is JobStatus.SUCCESS
        assert chunks in seen["body"]
        await orch.close()

    @pytest.mark.asyncio
    async def test_resume_waits_for_prior_worker_release(self, tmp_path: Path) -> None:
        from acheron.shell.step_handler import CachingStepHandler
        from acheron.shell.transports.local import LocalWorker

        release_started = asyncio.Event()
        allow_release = asyncio.Event()
        resumed_started = asyncio.Event()
        allow_resumed = asyncio.Event()
        calls = 0

        async def _worker_handler(_job: object) -> JobResult:
            nonlocal calls
            calls += 1
            if calls == 1:
                return JobResult(
                    job_id="job-resume-synthesize",
                    status=JobStatus.FAILED,
                    outputs=(),
                    metrics=JobMetrics(duration_seconds=0.0),
                    error="first execution failed",
                )
            resumed_started.set()
            await allow_resumed.wait()
            return JobResult(
                job_id="job-resume-synthesize",
                status=JobStatus.SUCCESS,
                outputs=(),
                metrics=JobMetrics(duration_seconds=0.0),
            )

        class BarrierHandler(CachingStepHandler):
            async def release_job(self, job_id: str) -> None:
                release_started.set()
                await allow_release.wait()
                await super().release_job(job_id)

        registry = InMemoryWorkerStore()
        await registry.register("tts-local", "local", "local", tts_caps("es"))
        worker = LocalWorker(
            worker_type=WorkerType.TTS,
            handler=_worker_handler,
            supported_languages_in=frozenset({"es"}),
            supported_languages_out=frozenset({"es"}),
        )
        handler = BarrierHandler(
            registry,
            worker_factory=lambda _registered: worker,
            data_dir=tmp_path,
        )
        plan = Plan(
            plan_id="plan-resume",
            job_id="job-resume",
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
                    payload={"chapter_id": "ch1"},
                    selected_worker_id="tts-local",
                ),
            ),
        )
        jobs = InMemoryJobStore()
        orch = Orchestrator(
            registry,
            PlanCache(tmp_path),
            handler=handler,
            job_store=jobs,
            step_cache=StepCache(tmp_path),
        )
        tracked = TrackedJob(
            job_id=plan.job_id,
            request=EpubRequest("book.epub", "en", "es"),
            strategy=ExecutorStrategy.STREAMING,
            plan=plan,
            status=PlanStatus.RUNNING,
        )
        await jobs.put(tracked)
        orch._track_execution_task(tracked)  # noqa: SLF001
        await release_started.wait()
        assert plan.job_id in orch._active_jobs  # noqa: SLF001
        persisted = await jobs.get(plan.job_id)
        assert persisted is not None
        assert persisted.status is PlanStatus.FAILED

        resumed = asyncio.create_task(orch.resume_job(plan.job_id))
        await asyncio.sleep(0)
        assert not resumed.done()

        allow_release.set()
        await resumed
        await resumed_started.wait()
        resumed_stream = await orch.events.subscribe(plan.job_id)
        allow_resumed.set()
        await asyncio.gather(*tuple(orch._tasks), return_exceptions=True)  # noqa: SLF001

        async def collect_resumed_events() -> list[JobLogEvent]:
            return [event async for event in iter_events(resumed_stream)]

        resumed_events = await asyncio.wait_for(collect_resumed_events(), timeout=1.0)
        assert any(event.step_id == "synthesize" for event in resumed_events)
        assert calls == 2
        assert plan.job_id not in orch._active_jobs  # noqa: SLF001
        await orch.close()

    @pytest.mark.asyncio
    async def test_resume_prior_execution_wait_is_bounded(self, tmp_path: Path) -> None:
        settings = Settings()
        settings.orchestrator.data_dir = tmp_path
        settings.orchestrator.shutdown_drain_seconds = 0.01
        jobs = InMemoryJobStore()
        plan = _single_step_plan("job-resume-timeout")
        await jobs.put(
            TrackedJob(
                job_id=plan.job_id,
                request=EpubRequest("book.epub", "en", "en"),
                strategy=ExecutorStrategy.STREAMING,
                plan=plan,
                status=PlanStatus.FAILED,
            )
        )
        orch = Orchestrator(
            InMemoryWorkerStore(),
            PlanCache(tmp_path),
            _success_handler,
            job_store=jobs,
            settings=settings,
        )

        async def _stuck() -> None:
            await asyncio.Event().wait()

        prior_execution = asyncio.create_task(_stuck())
        orch._execution_tasks[plan.job_id] = prior_execution  # noqa: SLF001
        try:
            with pytest.raises(TimeoutError):
                await orch.resume_job(plan.job_id)
        finally:
            prior_execution.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await prior_execution

    @pytest.mark.asyncio
    async def test_submit_job_preserves_handler_cache_until_registry_change(self, tmp_path: Path) -> None:
        """Transport resources persist across submissions and refresh on re-registration."""
        from acheron.shell.step_handler import CachingStepHandler

        reg = InMemoryWorkerStore()
        await reg.register("tts-1", "http://127.0.0.1:1", "http", tts_caps("es"))
        await reg.register("trans-1", "http://127.0.0.1:2", "http", translation_caps())
        orch = Orchestrator(reg, PlanCache(tmp_path))
        await orch.start()
        assert isinstance(orch._handler, CachingStepHandler)  # noqa: SLF001

        # First submit populates the handler's worker cache.
        await orch.submit_job(
            EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es"),
            ExecutorStrategy.STREAMING,
        )

        # Simulate a worker re-registering between jobs.
        await reg.register("tts-1", "http://127.0.0.1:99", "http", tts_caps("es"))

        # The second submission runs against the changed registry generation.
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
    async def test_start_rehydrates_handlers_for_persisted_local_workers(self, tmp_path: Path) -> None:
        """A restarted orchestrator can dispatch workers persisted by a previous process."""
        registry = InMemoryWorkerStore()
        first = Orchestrator(registry, PlanCache(tmp_path / "first"))
        await first.start()
        await first.shutdown()
        await first.close()

        restarted = Orchestrator(registry, PlanCache(tmp_path / "restarted"))
        await restarted.start()
        try:
            assert set(restarted._local_handlers) >= {  # noqa: SLF001
                "extraction-local",
                "chunking-local",
                "packaging-local",
            }
        finally:
            await restarted.shutdown()
            await restarted.close()

    @pytest.mark.asyncio
    async def test_preview_job_compiles_without_persistence(self, tmp_path: Path) -> None:
        """OPS-016: preview_job must compile a plan without creating a job record or a plan file."""
        registry = InMemoryWorkerStore()
        await registry.register("tts-1", "http://127.0.0.1:1", "http", tts_caps())
        await registry.register("trans-1", "http://127.0.0.1:2", "http", translation_caps())
        jobs = InMemoryJobStore()
        cache = PlanCache(tmp_path)
        orch = Orchestrator(registry, cache, job_store=jobs)
        await orch.start()
        try:
            plan = await orch.preview_job(
                EpubRequest("/input/book.epub", "en", "es"),
                ExecutorStrategy.STREAMING,
            )
            assert plan.steps
            assert await jobs.list_all() == ()
            assert not cache.plan_exists(plan.plan_id)
        finally:
            await orch.shutdown()
            await orch.close()

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
    async def test_submit_starts_broker_before_persisting_running_job(self, tmp_path: Path) -> None:
        from acheron.shell.job_events import JobEventBroker

        class ObservingJobStore(InMemoryJobStore):
            def __init__(self) -> None:
                super().__init__()
                self.broker: JobEventBroker | None = None
                self.observed_active: bool | None = None

            async def put(self, job: TrackedJob) -> None:
                if self.observed_active is None and job.status is PlanStatus.RUNNING:
                    assert self.broker is not None
                    self.observed_active = job.job_id in self.broker._active_jobs  # noqa: SLF001
                await super().put(job)

        jobs = ObservingJobStore()
        registry = InMemoryWorkerStore()
        await registry.register("tts-1", "http://127.0.0.1:1", "http", tts_caps())
        await registry.register("trans-1", "http://127.0.0.1:2", "http", translation_caps())
        orch = Orchestrator(registry, PlanCache(tmp_path), _success_handler, job_store=jobs)
        jobs.broker = orch.events
        await orch.start()
        try:
            await orch.submit_job(
                EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es"),
                ExecutorStrategy.STREAMING,
            )
        finally:
            await orch.shutdown()
            await orch.close()

        assert jobs.observed_active is True

    @pytest.mark.asyncio
    async def test_submit_persistence_failure_finishes_broker_state(self, tmp_path: Path) -> None:
        class FailingJobStore(InMemoryJobStore):
            async def put(self, job: TrackedJob) -> None:
                if job.status is PlanStatus.RUNNING:
                    raise RuntimeError("persistence failed")
                await super().put(job)

        jobs = FailingJobStore()
        registry = InMemoryWorkerStore()
        await registry.register("tts-1", "http://127.0.0.1:1", "http", tts_caps())
        await registry.register("trans-1", "http://127.0.0.1:2", "http", translation_caps())
        orch = Orchestrator(registry, PlanCache(tmp_path), _success_handler, job_store=jobs)
        await orch.start()
        try:
            with pytest.raises(RuntimeError, match="persistence failed"):
                await orch.submit_job(
                    EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es"),
                    ExecutorStrategy.STREAMING,
                )
            assert orch.events._active_jobs == set()  # noqa: SLF001
        finally:
            await orch.shutdown()
            await orch.close()

    @pytest.mark.asyncio
    async def test_resume_persistence_failure_restores_terminal_replay(self, tmp_path: Path) -> None:
        class FailingResumeStore(InMemoryJobStore):
            def __init__(self) -> None:
                super().__init__()
                self.persist_started = asyncio.Event()
                self.allow_failure = asyncio.Event()

            async def put(self, job: TrackedJob) -> None:
                if job.status is PlanStatus.RUNNING:
                    self.persist_started.set()
                    await self.allow_failure.wait()
                    raise RuntimeError("resume persistence failed")
                await super().put(job)

        job_id = "job-resume-restore"
        plan = _single_step_plan(job_id)
        tracked = TrackedJob(
            job_id=job_id,
            request=EpubRequest("/input/book.epub", "en", "es"),
            strategy=ExecutorStrategy.STREAMING,
            plan=plan,
            status=PlanStatus.FAILED,
        )
        jobs = FailingResumeStore()
        await jobs.put(tracked)
        orch = Orchestrator(InMemoryWorkerStore(), PlanCache(tmp_path), _success_handler, job_store=jobs)
        await orch.start()
        try:
            await orch._publish_event(tracked, "old terminal")  # noqa: SLF001
            await orch.events.finish(job_id)

            resume_task = asyncio.create_task(orch.resume_job(job_id))
            await asyncio.wait_for(jobs.persist_started.wait(), timeout=1.0)
            stream = await orch.events.subscribe(job_id)
            jobs.allow_failure.set()
            with pytest.raises(RuntimeError, match="resume persistence failed"):
                await resume_task

            async def collect_events() -> list[JobLogEvent]:
                return [event async for event in iter_events(stream)]

            events = await asyncio.wait_for(collect_events(), timeout=1.0)
            assert [event.message for event in events] == ["old terminal"]
            assert orch.events._subscribers == {}  # noqa: SLF001
            assert orch.events._active_jobs == set()  # noqa: SLF001

            late_stream = await orch.events.subscribe(job_id)

            async def collect_late_events() -> list[JobLogEvent]:
                return [event async for event in iter_events(late_stream)]

            late_events = await asyncio.wait_for(collect_late_events(), timeout=1.0)
            assert [event.message for event in late_events] == ["old terminal"]
        finally:
            jobs.allow_failure.set()
            await orch.shutdown()
            await orch.close()

    @pytest.mark.asyncio
    async def test_submit_retry_creates_linked_fresh_job(self, tmp_path: Path) -> None:
        jobs = InMemoryJobStore()
        registry = InMemoryWorkerStore()
        await registry.register("asr-1", "http://127.0.0.1:1", "http", asr_caps())
        await registry.register("trans-1", "http://127.0.0.1:2", "http", translation_caps())
        await registry.register("tts-1", "http://127.0.0.1:3", "http", tts_caps())
        orch = Orchestrator(registry, PlanCache(tmp_path), _success_handler, job_store=jobs)
        await orch.start()
        try:
            original = TrackedJob(
                job_id="job-old",
                request=AudioRequest("/input/book.wav", "en", "es", "whisper-v3"),
                strategy=ExecutorStrategy.STREAMING,
                label="atlas-ch1",
            )
            await jobs.put(original)

            retried = await orch.submit_retry(
                original.job_id,
                AudioRequest("/input/book.wav", "en", "es", "whisper-tiny"),
                ExecutorStrategy.STREAMING,
                label="atlas-retry",
            )

            assert retried.job_id != original.job_id
            assert retried.retries_from == original.job_id
            assert isinstance(retried.request, AudioRequest)
            assert retried.request.asr_model == "whisper-tiny"
            stored_original = await orch.get_job(original.job_id)
            assert stored_original is not None
            assert stored_original.retries_from is None
        finally:
            await orch.shutdown()
            await orch.close()

    @pytest.mark.asyncio
    async def test_submit_job_invalid_language_raises(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        reg = InMemoryWorkerStore()
        orch = Orchestrator(reg, PlanCache(tmp_path), _success_handler)
        await orch.start()

        request = EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es")
        with pytest.raises(InvalidLanguagePathError):
            await orch.submit_job(request, ExecutorStrategy.STREAMING)

    @pytest.mark.asyncio
    async def test_voice_selection_fails_before_persistence(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        reg = InMemoryWorkerStore()
        jobs = InMemoryJobStore()
        await reg.register("tts-es", "http://127.0.0.1:1", "http", tts_caps("es", metadata={"speakers": ["Vivian"]}))
        await reg.register("trans-1", "http://127.0.0.1:2", "http", translation_caps())
        orch = Orchestrator(reg, PlanCache(tmp_path), _success_handler, job_store=jobs)
        await orch.start()
        request = EpubRequest(
            source_path="/input/book.epub",
            source_language="en",
            target_language="es",
            voice="Ryan",
        )
        with pytest.raises(VoiceSelectionError, match=r"Ryan.*Vivian"):
            await orch.submit_job(request, ExecutorStrategy.STREAMING)
        assert await jobs.list_all() == ()
        assert not any(tmp_path.glob("plan-*/plan.json"))

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

    def test_record_step_progress_preserves_worker_attribution(self, tmp_path: Path) -> None:
        orch = Orchestrator(InMemoryWorkerStore(), PlanCache(tmp_path), _success_handler)
        plan = Plan(
            plan_id="plan-1",
            job_id="job-1",
            source_type="epub",
            source_language="en",
            target_language="es",
            executor_strategy=ExecutorStrategy.SEQUENTIAL,
            steps=(
                PlanStep(
                    step_id="step-2",
                    type=WorkerType.CHUNKING,
                    depends_on=(),
                    status=StepStatus.PENDING,
                    payload={},
                ),
                PlanStep(
                    step_id="step-3",
                    type=WorkerType.TTS,
                    depends_on=("step-2",),
                    status=StepStatus.PENDING,
                    payload={},
                ),
            ),
        )
        tracked = TrackedJob(
            job_id="job-1",
            request=EpubRequest("/input/book.epub", "en", "es"),
            strategy=ExecutorStrategy.SEQUENTIAL,
            plan=plan,
            status=PlanStatus.RUNNING,
        )
        result = JobResult(
            job_id="job-1",
            status=JobStatus.FAILED,
            outputs=(),
            metrics=JobMetrics(duration_seconds=1.0),
            error="input too long",
            worker_id="chunking-local",
        )

        orch._record_step_progress(tracked, plan, plan.steps[0], result)  # noqa: SLF001

        assert tracked.result is not None
        error = tracked.result.errors[0]
        assert error.step_id == "step-2"
        assert error.worker_type == WorkerType.CHUNKING
        assert error.worker_id == "chunking-local"
        assert error.message == "input too long"
        assert tracked.progress.current_step_id == "step-2"
        assert tracked.progress.current_worker_id == "chunking-local"

    def test_eta_uses_successful_durations_only(self, tmp_path: Path) -> None:
        orch = Orchestrator(InMemoryWorkerStore(), PlanCache(tmp_path), _success_handler)
        plan = Plan(
            plan_id="plan-eta",
            job_id="job-eta",
            source_type="epub",
            source_language="en",
            target_language="es",
            executor_strategy=ExecutorStrategy.SEQUENTIAL,
            steps=(
                PlanStep("step-1", WorkerType.EXTRACTION, (), StepStatus.PENDING, {}),
                PlanStep("step-2", WorkerType.TTS, ("step-1",), StepStatus.PENDING, {}),
            ),
        )
        tracked = TrackedJob(
            job_id=plan.job_id,
            request=EpubRequest("/input/book.epub", "en", "es"),
            strategy=ExecutorStrategy.SEQUENTIAL,
            plan=plan,
            status=PlanStatus.RUNNING,
        )

        orch._record_step_progress(  # noqa: SLF001
            tracked,
            plan,
            plan.steps[0],
            JobResult(
                job_id=plan.job_id,
                status=JobStatus.SUCCESS,
                outputs=(),
                metrics=JobMetrics(duration_seconds=2.0),
            ),
        )
        orch._record_step_progress(  # noqa: SLF001
            tracked,
            plan,
            plan.steps[1],
            JobResult(
                job_id=plan.job_id,
                status=JobStatus.FAILED,
                outputs=(),
                metrics=JobMetrics(duration_seconds=100.0),
                error="worker failed",
            ),
        )

        assert tracked.progress.successful_duration_seconds == 2.0
        assert tracked.progress.eta_seconds == 2.0

    @pytest.mark.asyncio
    async def test_progress_snapshot_is_set_while_handler_runs(self, tmp_path: Path) -> None:
        observed: list[tuple[str | None, WorkerType | None]] = []
        plan = _single_step_plan("job-1")
        tracked = TrackedJob(
            job_id="job-1",
            request=EpubRequest("/input/book.epub", "en", "en"),
            strategy=ExecutorStrategy.SEQUENTIAL,
            plan=plan,
            status=PlanStatus.RUNNING,
        )

        async def handler(_step: PlanStep, _plan: Plan) -> JobResult:
            observed.append((tracked.progress.current_step_id, tracked.progress.current_worker_type))
            return await _success_handler(_step, _plan)

        orch = Orchestrator(
            InMemoryWorkerStore(),
            PlanCache(tmp_path),
            handler,
            job_store=InMemoryJobStore(),
        )
        result = await orch._create_executor(tracked).run(plan)  # noqa: SLF001

        assert result.status is PlanStatus.COMPLETED
        assert observed == [("extract", WorkerType.EXTRACTION)]
        assert tracked.progress.current_step_id is None
        assert tracked.progress.completed_steps == 1
        assert tracked.progress.eta_seconds == 0.0

    @pytest.mark.asyncio
    async def test_persists_current_step_before_blocked_handler_returns(self, tmp_path: Path) -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        store = _ObservingJobStore()
        plan = _single_step_plan("job-blocked-progress")
        tracked = TrackedJob(
            job_id=plan.job_id,
            request=EpubRequest("/input/book.epub", "en", "en"),
            strategy=ExecutorStrategy.SEQUENTIAL,
            plan=plan,
            status=PlanStatus.RUNNING,
        )

        async def blocked_handler(_step: PlanStep, _plan: Plan) -> JobResult:
            started.set()
            await release.wait()
            return await _success_handler(_step, _plan)

        orch = Orchestrator(
            InMemoryWorkerStore(),
            PlanCache(tmp_path),
            blocked_handler,
            job_store=store,
        )
        execution = asyncio.create_task(orch._create_executor(tracked).run(plan))  # noqa: SLF001
        await started.wait()

        assert any(
            snapshot.progress.current_step_id == "extract" and snapshot.progress.completed_steps == 0
            for snapshot in store.snapshots
        )

        release.set()
        result = await execution
        assert result.status is PlanStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_streaming_cache_hits_update_and_persist_progress(self, tmp_path: Path) -> None:
        store = _ObservingJobStore()
        step_cache = InMemoryStepCache(tmp_path)
        plan = _single_step_plan("job-streaming-cache")
        cached_path = tmp_path / "cached.txt"
        cached_bytes = b"cached"
        cached_path.write_bytes(cached_bytes)
        cached = OutputFile(
            path=str(cached_path),
            filename="cached.txt",
            size_bytes=len(cached_bytes),
            checksum=hashlib.sha256(cached_bytes).hexdigest(),
            content_type="text/plain",
        )
        await step_cache.save_outputs(plan.job_id, "extract", (cached,))

        async def unexpected_handler(_step: PlanStep, _plan: Plan) -> JobResult:
            raise AssertionError("cached streaming step dispatched to worker")

        tracked = TrackedJob(
            job_id=plan.job_id,
            request=EpubRequest("/input/book.epub", "en", "en"),
            strategy=ExecutorStrategy.STREAMING,
            plan=plan,
            status=PlanStatus.RUNNING,
        )
        orch = Orchestrator(
            InMemoryWorkerStore(),
            PlanCache(tmp_path),
            unexpected_handler,
            job_store=store,
            step_cache=step_cache,
        )

        result = await orch._create_executor(tracked).run(plan)  # noqa: SLF001

        assert result.status is PlanStatus.COMPLETED
        assert tracked.progress.completed_steps == 1
        assert tracked.progress.eta_seconds == 0.0
        assert any(snapshot.progress.completed_steps == 1 for snapshot in store.snapshots)

    @pytest.mark.asyncio
    async def test_cancel_job_persists_partial_result(self, tmp_path: Path) -> None:
        handler_started = asyncio.Event()

        async def _blocking_handler(_step: PlanStep, _plan: Plan) -> JobResult:
            handler_started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        registry = InMemoryWorkerStore()
        await registry.register("tts-1", "http://127.0.0.1:1", "http", tts_caps())
        await registry.register("trans-1", "http://127.0.0.1:2", "http", translation_caps())
        jobs = InMemoryJobStore()
        orch = Orchestrator(registry, PlanCache(tmp_path), _blocking_handler, job_store=jobs)
        await orch.start()
        tracked = await orch.submit_job(
            EpubRequest(str(tmp_path / "book.epub"), "en", "es"),
            ExecutorStrategy.STREAMING,
        )
        await handler_started.wait()

        stream = await orch.events.subscribe(tracked.job_id)
        cancelled = await orch.cancel_job(tracked.job_id)

        async def collect_events() -> list[JobLogEvent]:
            return [event async for event in iter_events(stream)]

        events = await asyncio.wait_for(collect_events(), timeout=1.0)

        assert cancelled.status is PlanStatus.FAILED
        assert events[-1].message == "job cancelled"
        assert sum(event.status is PlanStatus.FAILED for event in events) == 1
        assert cancelled.result is not None
        assert cancelled.result.errors[0].message == "cancelled by operator"
        assert cancelled.result.completed_steps < cancelled.result.total_steps
        persisted = await jobs.get(tracked.job_id)
        assert persisted is not None
        assert persisted.status is PlanStatus.FAILED
        await orch.close()

    @pytest.mark.asyncio
    async def test_execute_cleanup_survives_cancellation_during_finish(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        handler_started = asyncio.Event()
        finish_started = asyncio.Event()
        allow_finish = asyncio.Event()

        async def _blocking_handler(_step: PlanStep, _plan: Plan) -> JobResult:
            handler_started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        registry = InMemoryWorkerStore()
        await registry.register("tts-1", "http://127.0.0.1:1", "http", tts_caps())
        await registry.register("trans-1", "http://127.0.0.1:2", "http", translation_caps())
        orch = Orchestrator(registry, PlanCache(tmp_path), _blocking_handler)
        await orch.start()
        tracked = await orch.submit_job(
            EpubRequest(str(tmp_path / "book.epub"), "en", "es"),
            ExecutorStrategy.STREAMING,
        )
        await handler_started.wait()
        stream = await orch.events.subscribe(tracked.job_id)
        original_finish = orch.events.finish

        async def delayed_finish(job_id: str) -> None:
            finish_started.set()
            await allow_finish.wait()
            await original_finish(job_id)

        monkeypatch.setattr(orch.events, "finish", delayed_finish)
        task = orch._execution_tasks[tracked.job_id]  # noqa: SLF001
        task.cancel("shutdown")
        await finish_started.wait()
        task.cancel("cancellation during cleanup")
        allow_finish.set()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        async def collect_events() -> list[JobLogEvent]:
            return [event async for event in iter_events(stream)]

        events = await asyncio.wait_for(collect_events(), timeout=1.0)
        assert events[-1].message == "job cancelled"
        assert tracked.job_id not in orch._active_jobs  # noqa: SLF001
        assert tracked.job_id not in orch.events._subscribers  # noqa: SLF001
        await orch.close()

    @pytest.mark.asyncio
    async def test_cancel_job_rejects_missing_and_terminal_jobs(self, tmp_path: Path) -> None:
        registry = InMemoryWorkerStore()
        await registry.register("tts-1", "http://127.0.0.1:1", "http", tts_caps())
        await registry.register("trans-1", "http://127.0.0.1:2", "http", translation_caps())
        orch = Orchestrator(registry, PlanCache(tmp_path), _success_handler)
        await orch.start()

        with pytest.raises(JobNotFoundError):
            await orch.cancel_job("job-missing")

        tracked = await orch.submit_job(
            EpubRequest(str(tmp_path / "book.epub"), "en", "es"),
            ExecutorStrategy.STREAMING,
        )
        await asyncio.gather(*tuple(orch._tasks), return_exceptions=True)  # noqa: SLF001
        completed = await orch.get_job(tracked.job_id)
        assert completed is not None
        assert completed.status is PlanStatus.COMPLETED

        with pytest.raises(JobNotCancellableError):
            await orch.cancel_job(tracked.job_id)
        await orch.close()

    @pytest.mark.asyncio
    async def test_cancel_job_waits_for_preexisting_background_persistence(self, tmp_path: Path) -> None:
        handler_started = asyncio.Event()

        async def _blocking_handler(_step: PlanStep, _plan: Plan) -> JobResult:
            handler_started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        registry = InMemoryWorkerStore()
        await registry.register("tts-1", "http://127.0.0.1:1", "http", tts_caps())
        await registry.register("trans-1", "http://127.0.0.1:2", "http", translation_caps())
        jobs = _DelayedBackgroundPutJobStore()
        orch = Orchestrator(registry, PlanCache(tmp_path), _blocking_handler, job_store=jobs)
        await orch.start()
        tracked = await orch.submit_job(
            EpubRequest(str(tmp_path / "book.epub"), "en", "es"),
            ExecutorStrategy.STREAMING,
        )
        await handler_started.wait()
        jobs.delay_enabled = True
        background_persist = asyncio.create_task(orch._persist_shielded(tracked))  # noqa: SLF001
        await jobs.persist_started.wait()

        cancellation = asyncio.create_task(orch.cancel_job(tracked.job_id))
        await asyncio.sleep(0)
        assert not cancellation.done()
        jobs.release_persist.set()
        cancelled = await cancellation
        await background_persist

        assert cancelled.status is PlanStatus.FAILED
        assert cancelled.result is not None
        assert cancelled.result.errors[0].message == "cancelled by operator"
        assert any(snapshot.status is PlanStatus.RUNNING for snapshot in jobs.snapshots)
        assert jobs.snapshots[-1].status is PlanStatus.FAILED
        persisted = await jobs.get(tracked.job_id)
        assert persisted is not None
        assert persisted.status is PlanStatus.FAILED
        await orch.close()

    @pytest.mark.asyncio
    async def test_cancel_job_waits_for_failed_persistence(self, tmp_path: Path) -> None:
        handler_started = asyncio.Event()

        async def _blocking_handler(_step: PlanStep, _plan: Plan) -> JobResult:
            handler_started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        registry = InMemoryWorkerStore()
        await registry.register("tts-1", "http://127.0.0.1:1", "http", tts_caps())
        await registry.register("trans-1", "http://127.0.0.1:2", "http", translation_caps())
        jobs = _DelayedCancellationPutJobStore()
        orch = Orchestrator(registry, PlanCache(tmp_path), _blocking_handler, job_store=jobs)
        await orch.start()
        tracked = await orch.submit_job(
            EpubRequest(str(tmp_path / "book.epub"), "en", "es"),
            ExecutorStrategy.STREAMING,
        )
        await handler_started.wait()

        cancellation = asyncio.create_task(orch.cancel_job(tracked.job_id))
        await jobs.persist_started.wait()
        assert not cancellation.done()
        jobs.release_persist.set()
        cancelled = await cancellation

        assert cancelled.status is PlanStatus.FAILED
        persisted = await jobs.get(tracked.job_id)
        assert persisted is not None
        assert persisted.status is PlanStatus.FAILED
        await orch.close()

    @pytest.mark.asyncio
    async def test_operator_cancel_propagates_persistence_failure(self, tmp_path: Path) -> None:
        handler_started = asyncio.Event()

        async def _blocking_handler(_step: PlanStep, _plan: Plan) -> JobResult:
            handler_started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        registry = InMemoryWorkerStore()
        await registry.register("tts-1", "http://127.0.0.1:1", "http", tts_caps())
        await registry.register("trans-1", "http://127.0.0.1:2", "http", translation_caps())
        orch = Orchestrator(
            registry,
            PlanCache(tmp_path),
            _blocking_handler,
            job_store=_KeyErrorOnReconciliationPutJobStore(),
        )
        await orch.start()
        tracked = await orch.submit_job(
            EpubRequest(str(tmp_path / "book.epub"), "en", "es"),
            ExecutorStrategy.STREAMING,
        )
        await handler_started.wait()

        with pytest.raises(KeyError):
            await orch.cancel_job(tracked.job_id)
        await orch.close()

    @pytest.mark.asyncio
    async def test_operator_cancel_resistant_handler_preserves_partial_result(self, tmp_path: Path) -> None:
        second_step_started = asyncio.Event()
        steps_completed = 0
        first_output = OutputFile(
            path=str(tmp_path / "first.txt"),
            filename="first.txt",
            size_bytes=5,
            checksum="first",
            content_type="text/plain",
        )

        async def _resistant_handler(step: PlanStep, plan: Plan) -> JobResult:
            nonlocal steps_completed
            if steps_completed == 1:
                second_step_started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    return JobResult(
                        job_id=plan.job_id,
                        status=JobStatus.SUCCESS,
                        outputs=(),
                        metrics=JobMetrics(
                            duration_seconds=99.0,
                            cost_estimate=CostEstimate(cost=99.0, basis=CostBasis.STUB),
                        ),
                    )
            steps_completed += 1
            return JobResult(
                job_id=plan.job_id,
                status=JobStatus.SUCCESS,
                outputs=(first_output,) if steps_completed == 1 else (),
                metrics=JobMetrics(
                    duration_seconds=2.0,
                    cost_estimate=CostEstimate(cost=3.0, basis=CostBasis.STUB),
                ),
            )

        registry = InMemoryWorkerStore()
        await registry.register("tts-1", "http://127.0.0.1:1", "http", tts_caps())
        await registry.register("trans-1", "http://127.0.0.1:2", "http", translation_caps())
        orch = Orchestrator(registry, PlanCache(tmp_path), _resistant_handler)
        await orch.start()
        tracked = await orch.submit_job(
            EpubRequest(str(tmp_path / "book.epub"), "en", "es"),
            ExecutorStrategy.STREAMING,
        )
        await second_step_started.wait()

        cancelled = await orch.cancel_job(tracked.job_id)

        assert cancelled.status is PlanStatus.FAILED
        assert cancelled.result is not None
        assert cancelled.result.completed_steps == 1
        assert cancelled.result.outputs == (first_output,)
        assert cancelled.result.total_cost == 3.0
        assert cancelled.result.total_duration_seconds == 2.0
        assert cancelled.progress.completed_steps == 1
        assert cancelled.result.errors[-1].message == "cancelled by operator"
        await orch.close()

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
        assert [error.message for error in persisted.result.errors] == ["execution cancelled during shutdown"]
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
    async def test_close_bounds_and_cancels_background_persist(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """CORR-042: close() must not wait indefinitely for reconciliation writes."""
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
        settings.orchestrator.shutdown_drain_seconds = 0.05
        job_store = _ControlledPutJobStore()
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

        with caplog.at_level("WARNING", logger="acheron.shell.orchestrator"):
            await asyncio.wait_for(orch.close(), timeout=0.5)
        assert job_store.persist_cancelled.is_set()
        assert any(tracked.job_id in record.message for record in caplog.records)

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
        job_store = _FailingReconciliationPutJobStore()
        orch = Orchestrator(reg, PlanCache(tmp_path), _success_handler, job_store=job_store)
        await orch.start()
        tracked = await orch.submit_job(
            EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es"),
            ExecutorStrategy.STREAMING,
        )
        # Pre-dispatch and per-step progress writes succeed; the terminal
        # completion write raises; the _execute recovery put must reconcile
        # the job to FAILED.
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
                metrics=JobMetrics(
                    duration_seconds=0.1,
                    cost_estimate=CostEstimate(cost=0.5, basis=CostBasis.STUB),
                ),
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
        orch = Orchestrator(
            reg, PlanCache(tmp_path), _blocking_handler, job_store=_KeyErrorOnReconciliationPutJobStore()
        )
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
    async def test_cancel_store_error_is_swallowed_after_reconciliation_failure(self, tmp_path: Path) -> None:
        """TEST-030: StoreError during cancellation persistence must not escape shutdown."""
        handler_started = asyncio.Event()

        async def _blocking_handler(_step: PlanStep, _plan: Plan) -> JobResult:
            handler_started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        reg = InMemoryWorkerStore()
        await reg.register("tts-1", "http://127.0.0.1:1", "http", tts_caps())
        await reg.register("trans-1", "http://127.0.0.1:2", "http", translation_caps())
        job_store = _StoreErrorOnReconciliationPutJobStore()
        orch = Orchestrator(reg, PlanCache(tmp_path), _blocking_handler, job_store=job_store)
        await orch.start()
        tracked = await orch.submit_job(
            EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es"),
            ExecutorStrategy.STREAMING,
        )
        await handler_started.wait()

        await orch.shutdown()

        assert tracked.status == PlanStatus.FAILED

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
    async def test_resume_invalidates_selected_steps_and_dependents(self, tmp_path: Path) -> None:
        cache = _RecordingStepCache(tmp_path)
        jobs = InMemoryJobStore()
        plan = Plan(
            plan_id="plan-resume",
            job_id="job-resume",
            source_type="epub",
            source_language="en",
            target_language="en",
            executor_strategy=ExecutorStrategy.SEQUENTIAL,
            steps=(
                PlanStep("extract", WorkerType.EXTRACTION, (), StepStatus.PENDING, {}),
                PlanStep("step-46", WorkerType.CHUNKING, ("extract",), StepStatus.PENDING, {"chapter_id": "ch46"}),
                PlanStep("step-47", WorkerType.CHUNKING, ("extract",), StepStatus.PENDING, {"chapter_id": "ch47"}),
                PlanStep("step-48", WorkerType.TTS, ("step-47",), StepStatus.PENDING, {"chapter_id": "ch47"}),
            ),
        )
        tracked = TrackedJob(
            job_id=plan.job_id,
            request=EpubRequest("/input/book.epub", "en", "en"),
            strategy=ExecutorStrategy.SEQUENTIAL,
            plan=plan,
            status=PlanStatus.FAILED,
        )
        orch = Orchestrator(
            InMemoryWorkerStore(),
            PlanCache(tmp_path),
            _success_handler,
            job_store=jobs,
            step_cache=cache,
        )
        await orch.start()
        await jobs.put(tracked)

        await orch.resume_job("job-resume", invalidate_steps=("step-47",))
        await asyncio.gather(*tuple(orch._tasks), return_exceptions=True)  # noqa: SLF001

        assert cache.invalidated == [{"step-47", "step-48"}]
        await orch.shutdown()

    @pytest.mark.asyncio
    async def test_resume_invalidates_chapter_and_rejects_unknown_target(self, tmp_path: Path) -> None:
        cache = _RecordingStepCache(tmp_path)
        jobs = InMemoryJobStore()
        plan = Plan(
            plan_id="plan-chapter",
            job_id="job-chapter",
            source_type="epub",
            source_language="en",
            target_language="en",
            executor_strategy=ExecutorStrategy.SEQUENTIAL,
            steps=(PlanStep("step-1", WorkerType.CHUNKING, (), StepStatus.PENDING, {"chapter_id": "ch1"}),),
        )
        await jobs.put(
            TrackedJob(
                job_id=plan.job_id,
                request=EpubRequest("/input/book.epub", "en", "en"),
                strategy=ExecutorStrategy.SEQUENTIAL,
                plan=plan,
                status=PlanStatus.FAILED,
            )
        )
        orch = Orchestrator(
            InMemoryWorkerStore(),
            PlanCache(tmp_path),
            _success_handler,
            job_store=jobs,
            step_cache=cache,
        )
        await orch.start()

        await orch.resume_job("job-chapter", invalidate_chapters=(1,))
        await asyncio.gather(*tuple(orch._tasks), return_exceptions=True)  # noqa: SLF001
        assert cache.invalidated == [{"step-1"}]

        await jobs.put(
            TrackedJob(
                job_id="job-unknown",
                request=EpubRequest("/input/book.epub", "en", "en"),
                strategy=ExecutorStrategy.SEQUENTIAL,
                plan=plan,
                status=PlanStatus.FAILED,
            )
        )
        with pytest.raises(InvalidationTargetError):
            await orch.resume_job("job-unknown", invalidate_steps=("missing",))
        await orch.shutdown()

    @pytest.mark.asyncio
    async def test_resume_job_reports_missing_chapter_metadata(self, tmp_path: Path) -> None:
        jobs = InMemoryJobStore()
        plan = _single_step_plan("job-no-chapter-metadata")
        await jobs.put(
            TrackedJob(
                job_id=plan.job_id,
                request=EpubRequest("/input/book.epub", "en", "en"),
                strategy=ExecutorStrategy.SEQUENTIAL,
                plan=plan,
                status=PlanStatus.FAILED,
            )
        )
        orch = Orchestrator(InMemoryWorkerStore(), PlanCache(tmp_path), _success_handler, job_store=jobs)
        await orch.start()

        with pytest.raises(InvalidationTargetError, match="Chapter metadata is unavailable"):
            await orch.resume_job(plan.job_id, invalidate_chapters=(1,))
        await orch.shutdown()

    @pytest.mark.asyncio
    async def test_resume_job_rejects_stale_running_job(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        jobs = InMemoryJobStore()
        orch = Orchestrator(InMemoryWorkerStore(), PlanCache(tmp_path), _success_handler, job_store=jobs)
        await orch.start()
        await jobs.put(
            TrackedJob(
                job_id="job-stale",
                request=EpubRequest("/input/book.epub", "en", "en"),
                strategy=ExecutorStrategy.SEQUENTIAL,
                plan=_single_step_plan("job-stale"),
                status=PlanStatus.RUNNING,
            )
        )

        with pytest.raises(JobAlreadyRunningError) as exc_info:
            await orch.resume_job("job-stale")
        assert exc_info.value.remediation == "acheron job cancel job-stale"
        await orch.shutdown()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [PlanStatus.PENDING, PlanStatus.COMPLETED])
    async def test_resume_job_rejects_non_terminal_incomplete_status(self, tmp_path, status) -> None:  # type: ignore[no-untyped-def]
        jobs = InMemoryJobStore()
        orch = Orchestrator(InMemoryWorkerStore(), PlanCache(tmp_path), _success_handler, job_store=jobs)
        await orch.start()
        job_id = f"job-{status.value}"
        await jobs.put(
            TrackedJob(
                job_id=job_id,
                request=EpubRequest("/input/book.epub", "en", "en"),
                strategy=ExecutorStrategy.SEQUENTIAL,
                plan=_single_step_plan(job_id),
                status=status,
            )
        )

        with pytest.raises(JobNotResumableError) as exc_info:
            await orch.resume_job(job_id)
        assert exc_info.value.remediation == f"acheron job status {job_id}"
        await orch.shutdown()

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

        with pytest.raises(JobAlreadyRunningError) as exc_info:
            await orch.resume_job(tracked.job_id)
        assert exc_info.value.remediation == "acheron job cancel " + tracked.job_id
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
    async def test_resume_job_without_plan_exposes_resubmission(self, tmp_path: Path) -> None:
        jobs = InMemoryJobStore()
        orch = Orchestrator(InMemoryWorkerStore(), PlanCache(tmp_path), _success_handler, job_store=jobs)
        await orch.start()
        await jobs.put(
            TrackedJob(
                job_id="job-no-plan",
                request=EpubRequest("/input/book.epub", "en", "en"),
                strategy=ExecutorStrategy.SEQUENTIAL,
                plan=None,
                status=PlanStatus.FAILED,
            )
        )

        with pytest.raises(NoPlanToResumeError) as exc_info:
            await orch.resume_job("job-no-plan")
        assert exc_info.value.remediation == "acheron job submit <source> --src ... --dest ..."
        await orch.shutdown()

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
        tracked.status = PlanStatus.FAILED
        await orch._job_store.put(tracked)  # noqa: SLF001
        handler_calls = 0
        progress: list[int] = []
        record_progress = orch._record_step_progress  # noqa: SLF001

        def capture_progress(
            tracked_job: TrackedJob,
            plan: Plan,
            step: PlanStep,
            result: JobResult,
        ) -> None:
            record_progress(tracked_job, plan, step, result)
            assert tracked_job.result is not None
            progress.append(tracked_job.result.completed_steps)

        monkeypatch.setattr(orch, "_record_step_progress", capture_progress)
        await orch.resume_job(tracked.job_id)

        # Wait for execute tasks
        tasks = list(orch._tasks)  # noqa: SLF001
        await asyncio.gather(*tasks)

        assert handler_calls == 0
        completed = await orch.get_job(tracked.job_id)
        assert completed is not None
        assert completed.status == PlanStatus.COMPLETED
        assert completed.result is not None
        assert completed.result.completed_steps == completed.result.total_steps
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
        assert [error.message for error in tracked.result.errors] == ["RuntimeError: <no message>"]

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

        with pytest.raises(RuntimeError, match="publicly-known"):
            OrchestratorSettings(data_dir=tmp_path, registration_token="dev-registration-token")

    @pytest.mark.asyncio
    async def test_orchestrator_rejects_short_registration_token(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """SEC-011: refuse to start with a token shorter than 32 chars."""
        from acheron.shell.config import OrchestratorSettings

        with pytest.raises(RuntimeError, match="too short"):
            OrchestratorSettings(data_dir=tmp_path, registration_token="short-token")

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
    settings.orchestrator.data_dir = tmp_path
    settings.providers.runpod.api_key = "rp-key"
    orch = Orchestrator(
        registry=InMemoryWorkerStore(),
        cache=PlanCache(tmp_path),
        job_store=InMemoryJobStore(),
        settings=settings,
    )
    assert orch._health_monitor._providers is not None  # noqa: SLF001
    assert orch._health_monitor._providers.get("runpod") is not None  # noqa: SLF001


def test_orchestrator_rejects_plan_cache_outside_settings_root(tmp_path: Path) -> None:
    """PlanCache must share the canonical settings data directory."""
    from acheron.shell.config import OrchestratorSettings

    settings = Settings(orchestrator=OrchestratorSettings(data_dir=tmp_path / "from_settings"))
    original_data_dir = settings.orchestrator.data_dir
    cache = PlanCache(data_dir=tmp_path / "from_cache")

    with pytest.raises(ValueError, match="canonical orchestrator data directory"):
        Orchestrator(
            registry=InMemoryWorkerStore(),
            cache=cache,
            job_store=InMemoryJobStore(),
            settings=settings,
        )

    assert settings.orchestrator.data_dir == original_data_dir


@pytest.mark.asyncio
async def test_cost_summary_excludes_numeric_unknown_costs(tmp_path: Path) -> None:
    jobs = InMemoryJobStore()
    orch = Orchestrator(InMemoryWorkerStore(), PlanCache(tmp_path), job_store=jobs)
    now = datetime.now(UTC)
    await jobs.put(
        TrackedJob(
            job_id="unknown-only",
            request=EpubRequest("/input/book.epub", "en", "es"),
            strategy=ExecutorStrategy.STREAMING,
            created_at=now,
            result=PlanResult(
                plan_id="unknown-plan",
                status=PlanStatus.FAILED,
                completed_steps=1,
                total_steps=1,
                outputs=(),
                total_cost=7.50,
                total_duration_seconds=1.0,
                total_cost_basis=CostBasis.UNKNOWN,
                cost_breakdown=(
                    CostBreakdown(
                        step_id="synthesize",
                        worker_type=WorkerType.TTS,
                        worker_id="tts-1",
                        gpu_seconds=None,
                        estimate=CostEstimate(cost=7.50, basis=CostBasis.UNKNOWN),
                    ),
                ),
            ),
            status=PlanStatus.FAILED,
        )
    )
    summary = await orch.get_cost_summary("all")
    assert summary.total_cost == 0.0
    assert summary.job_count == 1
    assert summary.unknown_cost_jobs == 1


@pytest.mark.asyncio
async def test_cost_summary_keeps_known_mixed_breakdown_costs(tmp_path: Path) -> None:
    jobs = InMemoryJobStore()
    orch = Orchestrator(InMemoryWorkerStore(), PlanCache(tmp_path), job_store=jobs)
    now = datetime.now(UTC)
    await jobs.put(
        TrackedJob(
            job_id="mixed-cost",
            request=EpubRequest("/input/book.epub", "en", "es"),
            strategy=ExecutorStrategy.STREAMING,
            created_at=now,
            result=PlanResult(
                plan_id="mixed-plan",
                status=PlanStatus.COMPLETED,
                completed_steps=2,
                total_steps=2,
                outputs=(),
                total_cost=99.99,
                total_duration_seconds=1.0,
                total_cost_basis=CostBasis.UNKNOWN,
                cost_breakdown=(
                    CostBreakdown(
                        step_id="translate",
                        worker_type=WorkerType.TRANSLATION,
                        worker_id="translation-1",
                        gpu_seconds=10.0,
                        estimate=CostEstimate(cost=1.25, basis=CostBasis.MEASURED),
                    ),
                    CostBreakdown(
                        step_id="synthesize",
                        worker_type=WorkerType.TTS,
                        worker_id="tts-1",
                        gpu_seconds=None,
                        estimate=CostEstimate(cost=98.74, basis=CostBasis.UNKNOWN),
                    ),
                ),
            ),
            status=PlanStatus.COMPLETED,
        )
    )
    summary = await orch.get_cost_summary("all")
    assert summary.total_cost == 1.25
    assert summary.job_count == 1
    assert summary.unknown_cost_jobs == 1


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
