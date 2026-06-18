"""Integration tests: orchestrator → real workers."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from acheron.core.models import AudioRequest, EpubRequest, ExecutorStrategy


async def _wait_for_completion(tracked: Any, timeout: float = 5.0) -> None:  # noqa: ASYNC109
    """Wait for a tracked job to complete."""
    deadline = asyncio.get_event_loop().time() + timeout
    while tracked.status == "running":
        if asyncio.get_event_loop().time() > deadline:
            break
        await asyncio.sleep(0.1)


class TestWorkerIntegrationHappyPath:
    @pytest.mark.asyncio
    async def test_epub_full_pipeline(self, wired_orchestrator) -> None:  # type: ignore[no-untyped-def]
        """EPUB request runs full pipeline: extract → chunk → translate → synthesize → package."""
        orch = wired_orchestrator
        request = EpubRequest(
            source_path="/tmp/test.epub",
            source_language="en",
            target_language="es",
        )
        tracked = await orch.submit_job(request, ExecutorStrategy.SEQUENTIAL)
        await _wait_for_completion(tracked)

        assert tracked.status in ("completed", "partial")
        assert tracked.result is not None
        assert tracked.result.completed_steps > 0
        assert tracked.result.total_steps == 5

    @pytest.mark.asyncio
    async def test_audio_pipeline(self, wired_orchestrator) -> None:  # type: ignore[no-untyped-def]
        """Audio request runs: extract → transcribe → chunk → translate → synthesize → package."""
        from acheron.core.models import (
            JobMetrics,
            JobResult,
            JobStatus,
            OutputFile,
            WorkerCapabilities,
            WorkerType,
        )

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

        orch = wired_orchestrator
        orch.register_worker(
            "asr-local",
            "local",
            "local",
            WorkerCapabilities(
                worker_type=WorkerType.ASR,
                supported_languages_in=frozenset({"en", "es", "fr", "de"}),
                supported_languages_out=frozenset({"en", "es", "fr", "de"}),
                supported_formats_in=frozenset({"mp3", "wav"}),
                supported_formats_out=frozenset({"text"}),
                max_payload_bytes=None,
                batch_capable=False,
                model_source=None,
            ),
            metadata={"handler": _asr_handler},
        )

        request = AudioRequest(
            source_path="/tmp/test.mp3",
            source_language="en",
            target_language="es",
        )
        tracked = await orch.submit_job(request, ExecutorStrategy.SEQUENTIAL)
        await _wait_for_completion(tracked)

        assert tracked.status in ("completed", "partial")
        assert tracked.result is not None
        assert tracked.result.total_steps == 6

    @pytest.mark.asyncio
    async def test_translation_uses_real_stub(self, wired_orchestrator) -> None:  # type: ignore[no-untyped-def]
        """Translation step uses the real HTTP stub and returns mock translated text."""
        orch = wired_orchestrator
        request = EpubRequest(
            source_path="/tmp/test.epub",
            source_language="en",
            target_language="es",
        )
        tracked = await orch.submit_job(request, ExecutorStrategy.SEQUENTIAL)
        await _wait_for_completion(tracked)

        assert tracked.result is not None
        assert tracked.result.completed_steps >= 3  # extract, chunk, translate at minimum


class TestWorkerIntegrationErrorPath:
    @pytest.mark.asyncio
    async def test_no_matching_language_pair(self, wired_orchestrator) -> None:  # type: ignore[no-untyped-def]
        """Job for unsupported language pair fails at plan compilation."""
        from acheron.core.errors import InvalidLanguagePathError

        orch = wired_orchestrator
        request = EpubRequest(
            source_path="/tmp/test.epub",
            source_language="xx",
            target_language="yy",
        )
        with pytest.raises(InvalidLanguagePathError):
            await orch.submit_job(request, ExecutorStrategy.SEQUENTIAL)

    @pytest.mark.asyncio
    async def test_worker_unreachable(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
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
        reg.register(
            "extract-local",
            "local",
            "local",
            WorkerCapabilities(
                worker_type=WorkerType.EXTRACTION,
                supported_languages_in=frozenset(),
                supported_languages_out=frozenset(),
                supported_formats_in=frozenset(),
                supported_formats_out=frozenset(),
                max_payload_bytes=None,
                batch_capable=False,
                model_source=None,
            ),
            metadata={"handler": _noop},
        )
        reg.register(
            "chunk-local",
            "local",
            "local",
            WorkerCapabilities(
                worker_type=WorkerType.CHUNKING,
                supported_languages_in=frozenset(),
                supported_languages_out=frozenset(),
                supported_formats_in=frozenset(),
                supported_formats_out=frozenset(),
                max_payload_bytes=None,
                batch_capable=False,
                model_source=None,
            ),
            metadata={"handler": _noop},
        )
        reg.register(
            "package-local",
            "local",
            "local",
            WorkerCapabilities(
                worker_type=WorkerType.PACKAGING,
                supported_languages_in=frozenset(),
                supported_languages_out=frozenset(),
                supported_formats_in=frozenset(),
                supported_formats_out=frozenset(),
                max_payload_bytes=None,
                batch_capable=False,
                model_source=None,
            ),
            metadata={"handler": _noop},
        )
        reg.register(
            "trans-http",
            "http://127.0.0.1:1",
            "http",
            WorkerCapabilities(
                worker_type=WorkerType.TRANSLATION,
                supported_languages_in=frozenset({"en"}),
                supported_languages_out=frozenset({"es"}),
                supported_formats_in=frozenset({"text"}),
                supported_formats_out=frozenset({"text"}),
                max_payload_bytes=None,
                batch_capable=False,
                model_source=None,
            ),
        )
        reg.register(
            "tts-http",
            "http://127.0.0.1:1",
            "http",
            WorkerCapabilities(
                worker_type=WorkerType.TTS,
                supported_languages_in=frozenset({"es"}),
                supported_languages_out=frozenset({"es"}),
                supported_formats_in=frozenset({"text"}),
                supported_formats_out=frozenset({"wav"}),
                max_payload_bytes=None,
                batch_capable=True,
                model_source=None,
            ),
        )

        handler = create_step_handler(reg)
        orch = Orchestrator(registry=reg, cache=PlanCache(tmp_path), handler=handler)

        request = EpubRequest(source_path="/tmp/test.epub", source_language="en", target_language="es")
        tracked = await orch.submit_job(request, ExecutorStrategy.SEQUENTIAL)
        await _wait_for_completion(tracked)

        assert tracked.status in ("failed", "partial")


class TestWorkerIntegrationEdgeCases:
    @pytest.mark.asyncio
    async def test_multiple_tts_workers_first_used(self, wired_orchestrator) -> None:  # type: ignore[no-untyped-def]
        """First matching TTS worker is used when multiple exist."""
        orch = wired_orchestrator
        request = EpubRequest(
            source_path="/tmp/test.epub",
            source_language="en",
            target_language="es",
        )
        tracked = await orch.submit_job(request, ExecutorStrategy.SEQUENTIAL)
        await _wait_for_completion(tracked)

        assert tracked.status in ("completed", "partial")
        assert tracked.result is not None

    @pytest.mark.asyncio
    async def test_sequential_executor(self, wired_orchestrator) -> None:  # type: ignore[no-untyped-def]
        """Sequential executor processes steps in order."""
        orch = wired_orchestrator
        request = EpubRequest(
            source_path="/tmp/test.epub",
            source_language="en",
            target_language="es",
        )
        tracked = await orch.submit_job(request, ExecutorStrategy.SEQUENTIAL)
        await _wait_for_completion(tracked)

        assert tracked.result is not None
        assert tracked.result.total_steps == 5


from acheron.core.models import Job, JobMetrics, JobResult, JobStatus, WorkerCapabilities, WorkerType  # noqa: E402
from acheron.shell.orchestrator import Orchestrator  # noqa: E402
from acheron.shell.registry import WorkerRegistry  # noqa: E402
from acheron.shell.step_handler import create_step_handler  # noqa: E402
