"""Tests for the orchestrator."""

from __future__ import annotations

import asyncio

import pytest

from acheron.core.errors import InvalidLanguagePathError, JobAlreadyRunningError, JobNotFoundError
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
from acheron.shell.cache import PlanCache
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
    async def test_sequential_executor_uses_step_cache(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
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
        await orch.resume_job(tracked.job_id)

        # Wait for execute tasks
        tasks = list(orch._tasks)  # noqa: SLF001
        await asyncio.gather(*tasks)

        assert handler_calls == 0

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

        # Pre-populate token file
        token_file = tmp_path / ".registration_token"
        token_file.write_text("pre-existing-token", encoding="utf-8")

        settings = Settings(orchestrator=OrchestratorSettings(data_dir=tmp_path, registration_token=None))
        orch = Orchestrator(InMemoryWorkerStore(), PlanCache(tmp_path), _success_handler, settings=settings)
        await orch.start()

        # Should load the pre-existing token
        assert orch.settings.orchestrator.registration_token == "pre-existing-token"

        # Clean up
        await orch.close()
        await orch.shutdown()


@pytest.mark.asyncio
async def test_orchestrator_constructs_health_providers_from_settings(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The Orchestrator must build HealthProviders from settings.providers.* API keys."""
    from acheron.shell.config import Settings
    from acheron.shell.orchestrator import Orchestrator
    from acheron.shell.cache import PlanCache
    from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore

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
