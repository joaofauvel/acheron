"""Tests for the HttpWorker transport."""

import hashlib
from pathlib import Path

import httpx
import pytest
import respx

from acheron.core.errors import WorkerError, WorkerUnavailableError
from acheron.core.models import (
    CostBasis,
    Job,
    JobStatus,
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


# A multipart/mixed response body the SDK edge would emit. Built statically
# so the test doesn't need the SDK to be importable.
_BOUNDARY = "acheron-test"


def _multipart_body(audio: bytes, metrics: bytes) -> bytes:
    audio_part = (
        (
            f"--{_BOUNDARY}\r\n"
            f'Content-Disposition: attachment; filename="ch1_0000.wav"\r\n'
            f"Content-Type: audio/wav\r\n"
            f'X-Acheron-Metadata: {{"sequence_id":0}}\r\n\r\n'
        ).encode()
        + audio
        + b"\r\n"
    )
    metrics_part = (f"--{_BOUNDARY}\r\nContent-Type: application/json\r\n\r\n").encode() + metrics + b"\r\n"
    closing = f"--{_BOUNDARY}--\r\n".encode()
    return audio_part + metrics_part + closing


class TestHttpWorkerExecuteMultipart:
    @respx.mock
    @pytest.mark.asyncio
    async def test_multipart_response_materializes_to_data_dir(self, tmp_path: Path) -> None:
        audio = b"\x00\x01\x02\x03" * 100
        metrics = (
            b'{"duration_seconds":1.5,"gpu_seconds":1.0,"tokens_in":null,'
            b'"tokens_out":null,"cost_estimate":0.042,"cost_basis":"measured"}'
        )
        body = _multipart_body(audio, metrics)
        respx.post(f"{_BASE_URL}/execute").mock(
            return_value=httpx.Response(
                200,
                content=body,
                headers={"content-type": f"multipart/mixed; boundary={_BOUNDARY}"},
            )
        )
        worker = HttpWorker(_BASE_URL, data_dir=tmp_path)
        job = Job(
            job_id="job-xyz-synthesize-ch1",
            job_type=WorkerType.TTS,
            payload={"chapter_id": "ch1"},
            chapter_id="ch1",
        )
        result = await worker.execute(job)
        assert result.status == JobStatus.SUCCESS
        assert result.job_id == "job-xyz-synthesize-ch1"
        assert len(result.outputs) == 1
        out = result.outputs[0]
        assert out.filename == "ch1_0000.wav"
        assert out.content_type == "audio/wav"
        assert out.size_bytes == len(audio)
        assert out.checksum == hashlib.sha256(audio).hexdigest()
        assert Path(out.path).read_bytes() == audio
        assert result.metrics.cost_estimate == 0.042
        assert result.metrics.cost_basis == CostBasis.MEASURED

    @respx.mock
    @pytest.mark.asyncio
    async def test_legacy_json_response_still_works(self) -> None:
        # Existing stub emits JSON with OutputFile.path. Ensure backward-compat.
        respx.post(f"{_BASE_URL}/execute").mock(
            return_value=httpx.Response(
                200,
                json={
                    "job_id": "j-1",
                    "status": "success",
                    "outputs": [
                        {
                            "path": "/tmp/x.wav",
                            "filename": "x.wav",
                            "size_bytes": 10,
                            "checksum": "0" * 64,
                            "content_type": "audio/wav",
                        }
                    ],
                    "metrics": {"duration_seconds": 1.5},
                    "error": None,
                },
            )
        )
        worker = HttpWorker(_BASE_URL)
        job = Job(job_id="j-1", job_type=WorkerType.TTS, payload={}, chapter_id="ch1")
        result = await worker.execute(job)
        assert result.status == JobStatus.SUCCESS
        assert result.outputs[0].filename == "x.wav"


class TestHttpWorkerStepCache:
    @respx.mock
    @pytest.mark.asyncio
    async def test_step_cache_default_constructs_from_data_dir(self, tmp_path: Path) -> None:
        """When step_cache is not provided, the worker constructs a default
        StepCache from data_dir (backward compat with pre-8b callers)."""
        from acheron.shell.cache import StepCache

        respx.post(f"{_BASE_URL}/execute").mock(
            return_value=httpx.Response(
                200,
                json={"job_id": "j-1", "status": "success", "outputs": [], "metrics": {}, "error": None},
            )
        )
        worker = HttpWorker(_BASE_URL, data_dir=tmp_path)
        assert isinstance(worker._step_cache, StepCache)  # noqa: SLF001
        assert worker._step_cache.data_dir == tmp_path  # noqa: SLF001

    @respx.mock
    @pytest.mark.asyncio
    async def test_explicit_step_cache_is_used(self, tmp_path: Path) -> None:
        from acheron.shell.cache import StepCache

        cache = StepCache(tmp_path / "other")
        respx.post(f"{_BASE_URL}/execute").mock(
            return_value=httpx.Response(
                200,
                json={"job_id": "j-1", "status": "success", "outputs": [], "metrics": {}, "error": None},
            )
        )
        worker = HttpWorker(_BASE_URL, data_dir=tmp_path, step_cache=cache)
        assert worker._step_cache is cache  # noqa: SLF001
        assert worker._step_cache.data_dir == tmp_path / "other"  # noqa: SLF001

    @respx.mock
    @pytest.mark.asyncio
    async def test_tts_path_uses_json_request(self, tmp_path: Path) -> None:
        """TTS job (non-ASR) still uses the JSON request path — no multipart.

        The new ASR branch in execute() must not affect the TTS / translation
        / chunking / packaging path. The wire request is ``application/json``
        and the response is the legacy ``JobResult`` JSON (or
        ``multipart/mixed``).
        """
        captured: dict = {}

        def _capture(request: httpx.Request) -> httpx.Response:
            captured["content_type"] = request.headers.get("content-type", "")
            captured["body"] = request.content
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content=b'{"job_id": "j-1", "status": "success", "outputs": [], "metrics": {"duration_seconds": 1.0}, "error": null}',
            )

        respx.post(f"{_BASE_URL}/execute").mock(side_effect=_capture)
        worker = HttpWorker(_BASE_URL, data_dir=tmp_path)
        job = Job(job_id="j-1", job_type=WorkerType.TTS, payload={}, chapter_id="ch1")
        result = await worker.execute(job)
        # TTS path uses application/json, NOT multipart/form-data.
        assert captured["content_type"].startswith("application/json")
        assert result.status == JobStatus.SUCCESS
