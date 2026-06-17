"""Tests for the HttpWorker transport."""

import httpx
import pytest
import respx

from acheron.core.errors import WorkerError, WorkerUnavailableError
from acheron.core.models import (
    BatchJob,
    BatchStatus,
    Job,
    JobMetrics,
    JobResult,
    JobStatus,
    WorkerCapabilities,
    WorkerType,
)
from acheron.shell.transports.http import HttpWorker

_BASE_URL = "http://worker:8000"


class TestHttpWorkerHealth:
    @respx.mock
    @pytest.mark.asyncio
    async def test_health_returns_true_on_200(self) -> None:
        respx.get(f"{_BASE_URL}/health").mock(return_value=httpx.Response(200))
        worker = HttpWorker(_BASE_URL)
        assert await worker.health() is True

    @respx.mock
    @pytest.mark.asyncio
    async def test_health_returns_false_on_500(self) -> None:
        respx.get(f"{_BASE_URL}/health").mock(return_value=httpx.Response(500))
        worker = HttpWorker(_BASE_URL)
        assert await worker.health() is False

    @respx.mock
    @pytest.mark.asyncio
    async def test_health_returns_false_on_connection_error(self) -> None:
        respx.get(f"{_BASE_URL}/health").mock(side_effect=httpx.ConnectError("refused"))
        worker = HttpWorker(_BASE_URL)
        assert await worker.health() is False


class TestHttpWorkerCapabilities:
    @respx.mock
    @pytest.mark.asyncio
    async def test_capabilities_returns_worker_caps(self) -> None:
        respx.get(f"{_BASE_URL}/capabilities").mock(
            return_value=httpx.Response(
                200,
                json={
                    "worker_type": "tts",
                    "supported_languages_in": ["es", "en"],
                    "supported_languages_out": ["es", "en"],
                    "supported_formats_in": ["text"],
                    "supported_formats_out": ["wav"],
                    "max_payload_bytes": None,
                    "batch_capable": True,
                    "model_source": "huggingface:Qwen/Qwen3-TTS",
                },
            )
        )
        worker = HttpWorker(_BASE_URL)
        caps = await worker.capabilities()
        assert caps.worker_type == WorkerType.TTS
        assert "es" in caps.supported_languages_in
        assert caps.batch_capable is True
        assert caps.model_source == "huggingface:Qwen/Qwen3-TTS"


class TestHttpWorkerExecute:
    @respx.mock
    @pytest.mark.asyncio
    async def test_execute_returns_job_result(self) -> None:
        respx.post(f"{_BASE_URL}/execute").mock(
            return_value=httpx.Response(
                200,
                json={
                    "job_id": "j-1",
                    "status": "success",
                    "outputs": [],
                    "metrics": {"duration_seconds": 1.5},
                    "error": None,
                },
            )
        )
        worker = HttpWorker(_BASE_URL)
        job = Job(job_id="j-1", job_type=WorkerType.TTS, payload={"text": "hola"}, chapter_id="ch1")
        result = await worker.execute(job)
        assert result.status == JobStatus.SUCCESS
        assert result.job_id == "j-1"
        assert result.metrics.duration_seconds == 1.5

    @respx.mock
    @pytest.mark.asyncio
    async def test_execute_raises_on_server_error(self) -> None:
        respx.post(f"{_BASE_URL}/execute").mock(return_value=httpx.Response(500, text="GPU OOM"))
        worker = HttpWorker(_BASE_URL)
        job = Job(job_id="j-1", job_type=WorkerType.TTS, payload={}, chapter_id="ch1")
        with pytest.raises(WorkerError, match="500"):
            await worker.execute(job)

    @respx.mock
    @pytest.mark.asyncio
    async def test_execute_raises_on_connection_error(self) -> None:
        respx.post(f"{_BASE_URL}/execute").mock(side_effect=httpx.ConnectError("refused"))
        worker = HttpWorker(_BASE_URL)
        job = Job(job_id="j-1", job_type=WorkerType.TTS, payload={}, chapter_id="ch1")
        with pytest.raises(WorkerUnavailableError):
            await worker.execute(job)


class TestHttpWorkerBatch:
    @respx.mock
    @pytest.mark.asyncio
    async def test_submit_batch_returns_handle(self) -> None:
        respx.post(f"{_BASE_URL}/submit-batch").mock(
            return_value=httpx.Response(200, json={"batch_handle": "batch-abc"})
        )
        worker = HttpWorker(_BASE_URL)
        batch = BatchJob(
            batch_id="b-1",
            jobs=(
                Job(job_id="j-1", job_type=WorkerType.TTS, payload={}, chapter_id="ch1"),
                Job(job_id="j-2", job_type=WorkerType.TTS, payload={}, chapter_id="ch1"),
            ),
        )
        handle = await worker.submit_batch(batch)
        assert handle == "batch-abc"

    @respx.mock
    @pytest.mark.asyncio
    async def test_poll_batch_returns_status(self) -> None:
        respx.get(f"{_BASE_URL}/poll/batch-abc").mock(
            return_value=httpx.Response(
                200,
                json={
                    "batch_id": "b-1",
                    "total": 2,
                    "completed": 1,
                    "failed": 0,
                    "pending": 1,
                    "results": [],
                },
            )
        )
        worker = HttpWorker(_BASE_URL)
        status = await worker.poll_batch("batch-abc")
        assert status.total == 2
        assert status.completed == 1
        assert status.pending == 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_collect_results_returns_completed(self) -> None:
        respx.get(f"{_BASE_URL}/poll/batch-abc").mock(
            return_value=httpx.Response(
                200,
                json={
                    "batch_id": "b-1",
                    "total": 1,
                    "completed": 1,
                    "failed": 0,
                    "pending": 0,
                    "results": [
                        {
                            "job_id": "j-1",
                            "status": "success",
                            "outputs": [],
                            "metrics": {"duration_seconds": 0.5},
                            "error": None,
                        }
                    ],
                },
            )
        )
        worker = HttpWorker(_BASE_URL)
        results = await worker.collect_results("batch-abc")
        assert len(results) == 1
        assert results[0].status == JobStatus.SUCCESS
