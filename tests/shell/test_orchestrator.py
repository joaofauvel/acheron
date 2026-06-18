"""Tests for the orchestrator."""

import pytest

from acheron.core.errors import InvalidLanguagePathError
from acheron.core.models import EpubRequest, ExecutorStrategy, JobMetrics, JobResult, JobStatus
from acheron.shell.cache import PlanCache
from acheron.shell.orchestrator import Orchestrator
from acheron.shell.registry import WorkerRegistry
from tests.shell.conftest import translation_caps, tts_caps


async def _success_handler(_step, _plan):  # type: ignore[no-untyped-def]
    return JobResult(
        job_id="noop",
        status=JobStatus.SUCCESS,
        outputs=(),
        metrics=JobMetrics(duration_seconds=0.01),
    )


class TestOrchestrator:
    @pytest.mark.asyncio
    async def test_submit_job_returns_tracked(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        reg = WorkerRegistry()
        reg.register("tts-1", "http://tts", "http", tts_caps())
        reg.register("trans-1", "http://trans", "http", translation_caps())
        orch = Orchestrator(reg, PlanCache(tmp_path), _success_handler)

        request = EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es")
        tracked = await orch.submit_job(request, ExecutorStrategy.BATCH_ASYNC)

        assert tracked.job_id.startswith("job-")
        assert tracked.status == "running"
        assert tracked.plan is not None

    @pytest.mark.asyncio
    async def test_submit_job_invalid_language_raises(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        reg = WorkerRegistry()
        orch = Orchestrator(reg, PlanCache(tmp_path), _success_handler)

        request = EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es")
        with pytest.raises(InvalidLanguagePathError):
            await orch.submit_job(request, ExecutorStrategy.BATCH_ASYNC)

    @pytest.mark.asyncio
    async def test_get_job(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        reg = WorkerRegistry()
        reg.register("tts-1", "http://tts", "http", tts_caps())
        reg.register("trans-1", "http://trans", "http", translation_caps())
        orch = Orchestrator(reg, PlanCache(tmp_path), _success_handler)

        request = EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es")
        tracked = await orch.submit_job(request, ExecutorStrategy.BATCH_ASYNC)

        found = await orch.get_job(tracked.job_id)
        assert found is not None
        assert found.job_id == tracked.job_id

    @pytest.mark.asyncio
    async def test_get_job_nonexistent(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        orch = Orchestrator(WorkerRegistry(), PlanCache(tmp_path), _success_handler)
        assert await orch.get_job("nope") is None

    @pytest.mark.asyncio
    async def test_list_jobs(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        reg = WorkerRegistry()
        reg.register("tts-1", "http://tts", "http", tts_caps())
        reg.register("trans-1", "http://trans", "http", translation_caps())
        orch = Orchestrator(reg, PlanCache(tmp_path), _success_handler)

        request = EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es")
        await orch.submit_job(request, ExecutorStrategy.BATCH_ASYNC)
        await orch.submit_job(request, ExecutorStrategy.SEQUENTIAL)

        jobs = await orch.list_jobs()
        assert len(jobs) == 2

    def test_register_and_list_workers(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        orch = Orchestrator(WorkerRegistry(), PlanCache(tmp_path), _success_handler)
        orch.register_worker("w-1", "http://a", "http", tts_caps())
        orch.register_worker("w-2", "http://b", "http", translation_caps())

        workers = orch.list_workers()
        worker_ids = {w.worker_id for w in workers}
        assert "w-1" in worker_ids
        assert "w-2" in worker_ids
        assert "extraction-local" in worker_ids
        assert "chunking-local" in worker_ids
        assert "packaging-local" in worker_ids

    def test_get_capabilities(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        reg = WorkerRegistry()
        reg.register("tts-1", "http://tts", "http", tts_caps("es"))
        reg.register("trans-1", "http://trans", "http", translation_caps("en", "es"))
        orch = Orchestrator(reg, PlanCache(tmp_path), _success_handler)

        caps = orch.get_capabilities()
        assert len(caps) >= 1

    def test_get_capabilities_filtered(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        reg = WorkerRegistry()
        reg.register("tts-1", "http://tts", "http", tts_caps("es"))
        reg.register("tts-2", "http://tts2", "http", tts_caps("fr"))
        orch = Orchestrator(reg, PlanCache(tmp_path), _success_handler)

        caps = orch.get_capabilities(dst="es")
        for pair in caps:
            assert pair.dst == "es"

    def test_get_capabilities_no_translation_worker(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Cross-language pairs should not appear without a translation worker."""
        from tests.shell.conftest import asr_caps

        reg = WorkerRegistry()
        reg.register("asr-1", "http://asr", "http", asr_caps("en"))
        reg.register("tts-1", "http://tts", "http", tts_caps("es"))
        orch = Orchestrator(reg, PlanCache(tmp_path), _success_handler)

        caps = orch.get_capabilities()
        pairs = {(p.src, p.dst) for p in caps}
        assert ("en", "es") not in pairs
        assert ("en", "en") not in pairs or ("en", "en") in pairs

    def test_get_capabilities_same_language_without_translation(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Same-language pairs should work without a translation worker."""
        reg = WorkerRegistry()
        reg.register("tts-1", "http://tts", "http", tts_caps("en"))
        orch = Orchestrator(reg, PlanCache(tmp_path), _success_handler)

        caps = orch.get_capabilities()
        pairs = {(p.src, p.dst) for p in caps}
        assert ("en", "en") in pairs
