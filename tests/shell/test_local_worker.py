"""Tests for the local worker transport."""

import pytest

from acheron.core.models import (
    Job,
    JobMetrics,
    JobResult,
    JobStatus,
    WorkerType,
)
from acheron.shell.transports.local import LocalWorker


async def _echo_handler(job: Job) -> JobResult:
    return JobResult(
        job_id=job.job_id,
        status=JobStatus.SUCCESS,
        outputs=(),
        metrics=JobMetrics(duration_seconds=0.0),
    )


async def _failing_handler(job: Job) -> JobResult:
    return JobResult(
        job_id=job.job_id,
        status=JobStatus.FAILED,
        outputs=(),
        metrics=JobMetrics(duration_seconds=0.0),
        error="handler failed",
    )


class TestLocalWorker:
    @pytest.mark.asyncio
    async def test_execute_delegates_to_handler(self) -> None:
        worker = LocalWorker(worker_type=WorkerType.TTS, handler=_echo_handler)
        job = Job(job_id="j-1", job_type=WorkerType.TTS, payload={}, chapter_id="ch1")
        result = await worker.execute(job)
        assert result.status == JobStatus.SUCCESS
        assert result.job_id == "j-1"

    @pytest.mark.asyncio
    async def test_execute_propagates_failure(self) -> None:
        worker = LocalWorker(worker_type=WorkerType.TTS, handler=_failing_handler)
        job = Job(job_id="j-2", job_type=WorkerType.TTS, payload={}, chapter_id="ch1")
        result = await worker.execute(job)
        assert result.status == JobStatus.FAILED
        assert result.error == "handler failed"

    @pytest.mark.asyncio
    async def test_health_always_true(self) -> None:
        worker = LocalWorker(worker_type=WorkerType.TTS, handler=_echo_handler)
        assert await worker.health() is True

    @pytest.mark.asyncio
    async def test_capabilities_reflect_constructor(self) -> None:
        worker = LocalWorker(
            worker_type=WorkerType.EXTRACTION,
            handler=_echo_handler,
            supported_languages_in=frozenset({"en"}),
            supported_languages_out=frozenset({"es"}),
            supported_formats_in=frozenset({"epub"}),
            supported_formats_out=frozenset({"md"}),
        )
        caps = await worker.capabilities()
        assert caps.worker_type == WorkerType.EXTRACTION
        assert "en" in caps.supported_languages_in
        assert "es" in caps.supported_languages_out
        assert "epub" in caps.supported_formats_in
        assert "md" in caps.supported_formats_out
        assert caps.batch_capable is False

    @pytest.mark.asyncio
    async def test_capabilities_defaults_empty(self) -> None:
        worker = LocalWorker(worker_type=WorkerType.CHUNKING, handler=_echo_handler)
        caps = await worker.capabilities()
        assert caps.worker_type == WorkerType.CHUNKING
        assert len(caps.supported_languages_in) == 0
        assert len(caps.supported_formats_out) == 0
