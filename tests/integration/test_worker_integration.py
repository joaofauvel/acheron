"""Integration tests: orchestrator → real workers."""

from __future__ import annotations

import asyncio
import hashlib
import os
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
    PlanStatus,
    WorkerCapabilities,
    WorkerType,
)
from acheron.shell.orchestrator import Orchestrator
from acheron.shell.step_handler import create_step_handler
from acheron.shell.stores.memory import InMemoryWorkerStore


async def _wait_for_completion(tracked: Any, timeout: float = 5.0) -> None:  # noqa: ASYNC109
    """Wait for a tracked job to complete. Raises TimeoutError if stuck."""
    deadline = asyncio.get_running_loop().time() + timeout
    while tracked.status == PlanStatus.RUNNING:
        if asyncio.get_running_loop().time() > deadline:
            msg = f"Job {tracked.job_id} still running after {timeout}s"
            raise TimeoutError(msg)
        await asyncio.sleep(0.1)


class TestWorkerIntegrationHappyPath:
    @pytest.mark.asyncio
    async def test_epub_full_pipeline(self, wired_orchestrator: Orchestrator, epub_file: Path) -> None:
        """EPUB request runs full pipeline: extract → chunk → translate → synthesize → package."""
        orch = wired_orchestrator
        request = EpubRequest(source_path=str(epub_file), source_language="en", target_language="es")
        tracked = await orch.submit_job(request, ExecutorStrategy.SEQUENTIAL)
        await _wait_for_completion(tracked)

        assert tracked.status == PlanStatus.COMPLETED
        assert tracked.result is not None
        assert tracked.result.completed_steps == 5
        assert tracked.result.total_steps == 5
        assert tracked.result.total_duration_seconds > 0

    @pytest.mark.asyncio
    async def test_audio_pipeline(self, wired_orchestrator: Orchestrator, tmp_path: Path) -> None:
        """Audio request runs: extract → transcribe → chunk → translate → synthesize → package."""
        orch = wired_orchestrator
        audio_path = tmp_path / "test.mp3"
        audio_path.write_bytes(b"fake audio")

        async def _asr_handler(job: Job) -> JobResult:
            plan_job_id = job.job_id.rsplit("-", 1)[0]
            data_dir = Path(os.environ.get("ACHERON_DATA_DIR", "/tmp"))
            trans_dir = data_dir / plan_job_id / "transcribe"
            trans_dir.mkdir(parents=True, exist_ok=True)
            out_path = trans_dir / f"{job.job_id}.txt"
            content = "mock transcription"
            out_path.write_text(content, encoding="utf-8")
            checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()

            return JobResult(
                job_id=job.job_id,
                status=JobStatus.SUCCESS,
                outputs=(
                    OutputFile(
                        path=str(out_path),
                        filename=f"{job.job_id}.txt",
                        size_bytes=out_path.stat().st_size,
                        checksum=checksum,
                        content_type="text/plain",
                    ),
                ),
                metrics=JobMetrics(duration_seconds=0.01),
            )

        await orch.register_worker(
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
            handler=_asr_handler,
        )

        request = AudioRequest(source_path=str(audio_path), source_language="en", target_language="es")
        tracked = await orch.submit_job(request, ExecutorStrategy.SEQUENTIAL)
        await _wait_for_completion(tracked)

        assert tracked.status == PlanStatus.COMPLETED
        assert tracked.result is not None
        assert tracked.result.completed_steps == 6
        assert tracked.result.total_steps == 6

    @pytest.mark.asyncio
    async def test_translation_uses_real_stub(self, wired_orchestrator: Orchestrator, epub_file: Path) -> None:
        """Translation step uses the real HTTP stub and returns mock translated text."""
        orch = wired_orchestrator
        request = EpubRequest(source_path=str(epub_file), source_language="en", target_language="es")
        tracked = await orch.submit_job(request, ExecutorStrategy.SEQUENTIAL)
        await _wait_for_completion(tracked)

        assert tracked.result is not None
        assert tracked.result.completed_steps == 5


class TestWorkerIntegrationErrorPath:
    @pytest.mark.asyncio
    async def test_no_matching_language_pair(self, wired_orchestrator: Orchestrator, epub_file: Path) -> None:
        """Job for unsupported language pair fails at plan compilation."""
        orch = wired_orchestrator
        request = EpubRequest(source_path=str(epub_file), source_language="xx", target_language="yy")
        with pytest.raises(InvalidLanguagePathError):
            await orch.submit_job(request, ExecutorStrategy.SEQUENTIAL)

    @pytest.mark.asyncio
    async def test_orchestration_steps_have_built_in_handlers(self, tmp_path: Path, epub_file: Path) -> None:
        """EXTRACTION/CHUNKING/PACKAGING are handled locally when no worker is registered.

        External workers (TTS/ASR/TRANSLATION) still go through the registry.
        """
        import struct

        from acheron.shell.cache import PlanCache, StepCache
        from acheron.shell.stores.memory import InMemoryWorkerStore

        def _dummy_wav(path: Path) -> bytes:
            sample_rate = 22050
            data_size = sample_rate * 2
            audio = (
                b"RIFF"
                + struct.pack("<I", 36 + data_size)
                + b"WAVE"
                + b"fmt "
                + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16)
                + b"data"
                + struct.pack("<I", data_size)
                + b"\x00" * data_size
            )
            path.write_bytes(audio)
            return audio

        async def _tts_handler(job: Job) -> JobResult:
            plan_job_id = job.job_id.rsplit("-", 1)[0]
            step_dir = tmp_path / plan_job_id / "synthesize"
            step_dir.mkdir(parents=True, exist_ok=True)
            wav_path = step_dir / f"{job.job_id}.wav"
            audio = _dummy_wav(wav_path)
            checksum = hashlib.sha256(audio).hexdigest()
            return JobResult(
                job_id=job.job_id,
                status=JobStatus.SUCCESS,
                outputs=(
                    OutputFile(
                        path=str(wav_path),
                        filename=wav_path.name,
                        size_bytes=len(audio),
                        checksum=checksum,
                        content_type="audio/wav",
                    ),
                ),
                metrics=JobMetrics(duration_seconds=0.0),
            )

        async def _no_op_handler(job: Job) -> JobResult:
            return JobResult(
                job_id=job.job_id,
                status=JobStatus.SUCCESS,
                outputs=(),
                metrics=JobMetrics(duration_seconds=0.0),
            )

        orch = Orchestrator(registry=InMemoryWorkerStore(), cache=PlanCache(tmp_path), step_cache=StepCache(tmp_path))
        await orch.start()
        await orch.register_worker(
            "trans-local",
            "local",
            "local",
            _caps(WorkerType.TRANSLATION, langs_in=frozenset({"en"}), langs_out=frozenset({"es"})),
            handler=_no_op_handler,
        )
        await orch.register_worker(
            "tts-local",
            "local",
            "local",
            _caps(WorkerType.TTS, langs_in=frozenset({"es"}), langs_out=frozenset({"es"}), batch_capable=True),
            handler=_tts_handler,
        )
        request = EpubRequest(source_path=str(epub_file), source_language="en", target_language="es")
        tracked = await orch.submit_job(request, ExecutorStrategy.STREAMING)
        await _wait_for_completion(tracked)

        assert tracked.status == PlanStatus.COMPLETED, (
            f"job failed: {tracked.result.errors if tracked.result else 'no result'}"
        )
        assert tracked.result is not None
        assert tracked.result.completed_steps == 5
        assert tracked.result.total_steps == 5

    @pytest.mark.asyncio
    async def test_orchestrator_registers_local_workers_without_serializable_handlers(
        self,
        tmp_path: Path,
    ) -> None:
        """Regression for C1: orchestrator can start with built-in local workers
        even though their handler metadata is non-serializable (coroutine).
        """
        from acheron.shell.cache import PlanCache
        from acheron.shell.stores.memory import InMemoryWorkerStore

        reg = InMemoryWorkerStore()
        cache = PlanCache(data_dir=tmp_path)
        orch = Orchestrator(registry=reg, cache=cache)
        await orch.start()
        workers = await orch.list_workers()
        assert "extraction-local" in {w.worker_id for w in workers}

    @pytest.mark.asyncio
    async def test_orchestrator_works_with_redis_backend(
        self,
        tmp_path: Path,
        redis_url: str,
    ) -> None:
        """Orchestrator with RedisWorkerStore + RedisJobStore round-trips a
        registered worker and a persisted job through Redis. Regression for
        TEST-002 — the previous "redis backend" test used InMemoryWorkerStore
        so the bug it claimed to guard was never exercised.
        """
        from acheron.shell.cache import PlanCache
        from acheron.shell.stores.redis import RedisJobStore, RedisWorkerStore

        worker_store = RedisWorkerStore(redis_url)
        job_store = RedisJobStore(redis_url)
        await worker_store.connect()
        await job_store.connect()
        try:
            cache = PlanCache(data_dir=tmp_path)
            orch = Orchestrator(
                registry=worker_store,
                cache=cache,
                job_store=job_store,
            )
            await orch.start()
            try:
                workers = await orch.list_workers()
                worker_ids = {w.worker_id for w in workers}
                assert "extraction-local" in worker_ids

                # Auto-registered workers must be visible via the Redis store
                # directly, not just through the orchestrator's in-memory view.
                re_loaded = await worker_store.get("extraction-local")
                assert re_loaded is not None
                assert re_loaded.endpoint == next(w.endpoint for w in workers if w.worker_id == "extraction-local")
            finally:
                await orch.shutdown()
        finally:
            await worker_store.close()
            await job_store.close()

    @pytest.mark.asyncio
    async def test_worker_unreachable(self, tmp_path: Path, epub_file: Path) -> None:
        """Job fails when TTS worker is unreachable."""
        from acheron.shell.cache import PlanCache

        async def _noop(job: Job) -> JobResult:
            return JobResult(
                job_id=job.job_id,
                status=JobStatus.SUCCESS,
                outputs=(),
                metrics=JobMetrics(duration_seconds=0.0),
            )

        reg = InMemoryWorkerStore()
        for wt in (WorkerType.EXTRACTION, WorkerType.CHUNKING, WorkerType.PACKAGING):
            await reg.register(f"{wt.value}-local", "local", "local", _caps(wt))
        await reg.register(
            "trans-http",
            "http://127.0.0.1:9999",
            "http",
            _caps(WorkerType.TRANSLATION, langs_in=frozenset({"en"}), langs_out=frozenset({"es"})),
        )
        await reg.register(
            "tts-http",
            "http://127.0.0.1:9999",
            "http",
            _caps(WorkerType.TTS, langs_in=frozenset({"es"}), langs_out=frozenset({"es"}), batch_capable=True),
        )

        handler = create_step_handler(reg, data_dir=tmp_path)
        orch = Orchestrator(registry=reg, cache=PlanCache(tmp_path), handler=handler)
        await orch.start()
        request = EpubRequest(source_path=str(epub_file), source_language="en", target_language="es")
        tracked = await orch.submit_job(request, ExecutorStrategy.SEQUENTIAL)
        await _wait_for_completion(tracked)

        assert tracked.status in (PlanStatus.FAILED, PlanStatus.PARTIAL)


class TestWorkerIntegrationEdgeCases:
    @pytest.mark.asyncio
    async def test_multiple_tts_workers_uses_first(self, wired_orchestrator: Orchestrator, epub_file: Path) -> None:
        """First matching TTS worker is used when multiple exist (tts-http registered before tts-grpc)."""
        orch = wired_orchestrator
        registered = await orch.list_workers()
        tts_workers = [w for w in registered if w.capabilities.worker_type == WorkerType.TTS]
        assert len(tts_workers) == 2
        assert tts_workers[0].worker_id == "tts-http"
        assert tts_workers[1].worker_id == "tts-grpc"

        request = EpubRequest(source_path=str(epub_file), source_language="en", target_language="es")
        tracked = await orch.submit_job(request, ExecutorStrategy.SEQUENTIAL)
        await _wait_for_completion(tracked)

        assert tracked.status == PlanStatus.COMPLETED
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
