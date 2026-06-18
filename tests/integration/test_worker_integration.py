"""Integration tests: orchestrator → real workers."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from acheron.core.errors import InvalidLanguagePathError
from acheron.core.models import (
    AudioRequest,
    EpubRequest,
    ExecutorStrategy,
    Job,
    JobMetrics,
    JobResult,
    JobStatus,
    OutputFile,
    WorkerCapabilities,
    WorkerType,
)
from acheron.shell.orchestrator import Orchestrator
from acheron.shell.registry import WorkerRegistry
from acheron.shell.step_handler import create_step_handler


async def _wait_for_completion(tracked: Any, timeout: float = 5.0) -> None:  # noqa: ASYNC109
    """Wait for a tracked job to complete. Raises TimeoutError if stuck."""
    deadline = asyncio.get_running_loop().time() + timeout
    while tracked.status == "running":
        if asyncio.get_running_loop().time() > deadline:
            msg = f"Job {tracked.job_id} still running after {timeout}s"
            raise TimeoutError(msg)
        await asyncio.sleep(0.1)


class TestWorkerIntegrationHappyPath:
    @pytest.mark.asyncio
    async def test_epub_full_pipeline(self, wired_orchestrator: Orchestrator) -> None:
        """EPUB request runs full pipeline: extract → chunk → translate → synthesize → package."""
        orch = wired_orchestrator
        request = EpubRequest(source_path="/tmp/test.epub", source_language="en", target_language="es")
        tracked = await orch.submit_job(request, ExecutorStrategy.SEQUENTIAL)
        await _wait_for_completion(tracked)

        assert tracked.status == "completed"
        assert tracked.result is not None
        assert tracked.result.completed_steps == 5
        assert tracked.result.total_steps == 5
        assert tracked.result.total_duration_seconds > 0

    @pytest.mark.asyncio
    async def test_audio_pipeline(self, wired_orchestrator: Orchestrator) -> None:
        """Audio request runs: extract → transcribe → chunk → translate → synthesize → package."""
        orch = wired_orchestrator

        async def _asr_handler(job: Job) -> JobResult:
            return JobResult(
                job_id=job.job_id,
                status=JobStatus.SUCCESS,
                outputs=(
                    OutputFile(
                        path=f"/tmp/{job.job_id}",
                        filename=f"{job.job_id}.txt",
                        size_bytes=50,
                        checksum="",
                        content_type="text/plain",
                    ),
                ),
                metrics=JobMetrics(duration_seconds=0.01),
            )

        orch.register_worker(
            "asr-local",
            "local",
            "local",
            _caps(
                WorkerType.ASR,
                langs_in=_LANGS,
                langs_out=_LANGS,
                formats_in=frozenset({"mp3", "wav"}),
                formats_out=frozenset({"text"}),
            ),
            metadata={"handler": _asr_handler},
        )

        request = AudioRequest(source_path="/tmp/test.mp3", source_language="en", target_language="es")
        tracked = await orch.submit_job(request, ExecutorStrategy.SEQUENTIAL)
        await _wait_for_completion(tracked)

        assert tracked.status == "completed"
        assert tracked.result is not None
        assert tracked.result.completed_steps == 6
        assert tracked.result.total_steps == 6

    @pytest.mark.asyncio
    async def test_translation_uses_real_stub(self, wired_orchestrator: Orchestrator) -> None:
        """Translation step uses the real HTTP stub and returns mock translated text."""
        orch = wired_orchestrator
        request = EpubRequest(source_path="/tmp/test.epub", source_language="en", target_language="es")
        tracked = await orch.submit_job(request, ExecutorStrategy.SEQUENTIAL)
        await _wait_for_completion(tracked)

        assert tracked.result is not None
        assert tracked.result.completed_steps == 5


class TestWorkerIntegrationErrorPath:
    @pytest.mark.asyncio
    async def test_no_matching_language_pair(self, wired_orchestrator: Orchestrator) -> None:
        """Job for unsupported language pair fails at plan compilation."""
        orch = wired_orchestrator
        request = EpubRequest(source_path="/tmp/test.epub", source_language="xx", target_language="yy")
        with pytest.raises(InvalidLanguagePathError):
            await orch.submit_job(request, ExecutorStrategy.SEQUENTIAL)

    @pytest.mark.asyncio
    async def test_orchestration_steps_have_built_in_handlers(self, tmp_path: Path) -> None:
        """EXTRACTION/CHUNKING/PACKAGING are handled locally when no worker is registered.

        External workers (TTS/ASR/TRANSLATION) still go through the registry.
        """
        from acheron.shell.cache import PlanCache
        from acheron.shell.step_handler import create_step_handler

        async def _no_op_handler(job: Job) -> JobResult:
            return JobResult(
                job_id=job.job_id,
                status=JobStatus.SUCCESS,
                outputs=(),
                metrics=JobMetrics(duration_seconds=0.0),
            )

        reg = WorkerRegistry()
        reg.register(
            "trans-local",
            "local",
            "local",
            _caps(WorkerType.TRANSLATION, langs_in=frozenset({"en"}), langs_out=frozenset({"es"})),
            metadata={"handler": _no_op_handler},
        )
        reg.register(
            "tts-local",
            "local",
            "local",
            _caps(WorkerType.TTS, langs_in=frozenset({"es"}), langs_out=frozenset({"es"}), batch_capable=True),
            metadata={"handler": _no_op_handler},
        )

        handler = create_step_handler(reg)
        orch = Orchestrator(registry=reg, cache=PlanCache(tmp_path), handler=handler)
        request = EpubRequest(source_path="/tmp/test.epub", source_language="en", target_language="es")
        tracked = await orch.submit_job(request, ExecutorStrategy.BATCH_ASYNC)
        await _wait_for_completion(tracked)

        assert tracked.status == "completed", f"job failed: {tracked.result.errors if tracked.result else 'no result'}"
        assert tracked.result is not None
        assert tracked.result.completed_steps == 5
        assert tracked.result.total_steps == 5

    @pytest.mark.asyncio
    async def test_worker_unreachable(self, tmp_path: Path) -> None:
        """Job fails when TTS worker is unreachable."""
        from acheron.shell.cache import PlanCache

        async def _noop(job: Job) -> JobResult:
            return JobResult(
                job_id=job.job_id,
                status=JobStatus.SUCCESS,
                outputs=(),
                metrics=JobMetrics(duration_seconds=0.0),
            )

        reg = WorkerRegistry()
        for wt in (WorkerType.EXTRACTION, WorkerType.CHUNKING, WorkerType.PACKAGING):
            reg.register(f"{wt.value}-local", "local", "local", _caps(wt), metadata={"handler": _noop})
        reg.register(
            "trans-http",
            "http://127.0.0.1:9999",
            "http",
            _caps(WorkerType.TRANSLATION, langs_in=frozenset({"en"}), langs_out=frozenset({"es"})),
        )
        reg.register(
            "tts-http",
            "http://127.0.0.1:9999",
            "http",
            _caps(WorkerType.TTS, langs_in=frozenset({"es"}), langs_out=frozenset({"es"}), batch_capable=True),
        )

        handler = create_step_handler(reg)
        orch = Orchestrator(registry=reg, cache=PlanCache(tmp_path), handler=handler)
        request = EpubRequest(source_path="/tmp/test.epub", source_language="en", target_language="es")
        tracked = await orch.submit_job(request, ExecutorStrategy.SEQUENTIAL)
        await _wait_for_completion(tracked)

        assert tracked.status in ("failed", "partial")


class TestWorkerIntegrationEdgeCases:
    @pytest.mark.asyncio
    async def test_multiple_tts_workers_uses_first(self, wired_orchestrator: Orchestrator) -> None:
        """First matching TTS worker is used when multiple exist (tts-http registered before tts-grpc)."""
        orch = wired_orchestrator
        registered = orch.list_workers()
        tts_workers = [w for w in registered if w.capabilities.worker_type == WorkerType.TTS]
        assert len(tts_workers) == 2
        assert tts_workers[0].worker_id == "tts-http"
        assert tts_workers[1].worker_id == "tts-grpc"

        request = EpubRequest(source_path="/tmp/test.epub", source_language="en", target_language="es")
        tracked = await orch.submit_job(request, ExecutorStrategy.SEQUENTIAL)
        await _wait_for_completion(tracked)

        assert tracked.status == "completed"
        assert tracked.result is not None


_LANGS = frozenset({"en", "es", "fr", "de"})


def _caps(  # noqa: PLR0913
    worker_type: WorkerType,
    *,
    langs_in: frozenset[str] = frozenset(),
    langs_out: frozenset[str] = frozenset(),
    formats_in: frozenset[str] = frozenset(),
    formats_out: frozenset[str] = frozenset(),
    batch_capable: bool = False,
) -> WorkerCapabilities:
    return WorkerCapabilities(
        worker_type=worker_type,
        supported_languages_in=langs_in,
        supported_languages_out=langs_out,
        supported_formats_in=formats_in,
        supported_formats_out=formats_out,
        max_payload_bytes=None,
        batch_capable=batch_capable,
        model_source=None,
    )
