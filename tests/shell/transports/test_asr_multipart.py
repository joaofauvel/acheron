"""E2E test for HttpWorker._execute_asr_multipart driving the ASR stub."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from acheron.core.models import Job, JobStatus, OutputFile, WorkerType
from acheron.shell.cache import StepCache
from acheron.shell.transports.http import HttpWorker


def _audio_bytes() -> bytes:
    return b"\xff\xfb\x90\x00MOCK-MP3-AUDIO"


async def _seed_extract_output(cache: StepCache, plan_job_id: str, audio_path: Path) -> None:
    out = OutputFile(
        path=str(audio_path),
        filename=audio_path.name,
        size_bytes=audio_path.stat().st_size,
        checksum="x" * 64,
        content_type="audio/mpeg",
    )
    await cache.save_outputs(plan_job_id, "extract", (out,))


@pytest.fixture
def audio_file(tmp_path: Path) -> Path:
    p = tmp_path / "podcast.mp3"
    p.write_bytes(_audio_bytes())
    return p


@pytest.mark.asyncio
async def test_asr_multipart_success(tmp_path: Path, audio_file: Path) -> None:
    """ASR step sends multipart; stub returns a text/plain transcript."""
    plan_job_id = "job-abc123"
    cache = StepCache(tmp_path)
    await _seed_extract_output(cache, plan_job_id, audio_file)

    captured: dict = {}

    async def _handle(request: httpx.Request) -> httpx.Response:
        captured["content_type"] = request.headers.get("content-type", "")
        captured["body"] = await request.aread()
        return httpx.Response(
            200,
            headers={"content-type": "multipart/mixed; boundary=----x"},
            content=(
                b"------x\r\n"
                b'Content-Disposition: attachment; filename="ch1.txt"\r\n'
                b"Content-Type: text/plain\r\n\r\n"
                b"transcribed audio\r\n"
                b"------x\r\n"
                b"Content-Type: application/json\r\n\r\n"
                b'{"duration_seconds": 1.5, "cost_basis": null}\r\n'
                b"------x--\r\n"
            ),
        )

    transport = httpx.MockTransport(_handle)
    async with httpx.AsyncClient(transport=transport, base_url="http://stub:8002") as client:
        worker = HttpWorker(
            "http://stub:8002",
            client=client,
            data_dir=tmp_path,
            step_cache=cache,
        )

        job = Job(
            job_id=f"{plan_job_id}-transcribe",
            job_type=WorkerType.ASR,
            payload={"source_language": "en"},
            chapter_id="ch1",
        )
        result = await worker.execute(job)
    assert result.status == JobStatus.SUCCESS
    assert any(o.content_type == "text/plain" for o in result.outputs)
    # The bytes that reached the stub include the audio file's contents.
    assert b"MOCK-MP3-AUDIO" in captured["body"]
    # The request is multipart, not application/json.
    assert captured["content_type"].startswith("multipart/form-data")


@pytest.mark.asyncio
async def test_asr_multipart_missing_extract(tmp_path: Path) -> None:
    """No extract step output → WorkerError."""
    from acheron.core.errors import WorkerError

    cache = StepCache(tmp_path)
    transport = httpx.MockTransport(lambda _r: httpx.Response(200))
    async with httpx.AsyncClient(transport=transport, base_url="http://stub:8002") as client:
        worker = HttpWorker(
            "http://stub:8002",
            client=client,
            data_dir=tmp_path,
            step_cache=cache,
        )
        job = Job(
            job_id="job-xyz-transcribe",
            job_type=WorkerType.ASR,
            payload={"source_language": "en"},
            chapter_id="ch1",
        )
        with pytest.raises(WorkerError, match="no extract step output"):
            await worker.execute(job)


@pytest.mark.asyncio
async def test_asr_multipart_no_audio_in_extract(tmp_path: Path) -> None:
    """Extract step produced only text (not audio) → WorkerError."""
    from acheron.core.errors import WorkerError

    plan_job_id = "job-abc123"
    cache = StepCache(tmp_path)
    text_out = OutputFile(
        path=str(tmp_path / "chapter.txt"),
        filename="chapter.txt",
        size_bytes=0,
        checksum="x" * 64,
        content_type="text/plain",
    )
    await cache.save_outputs(plan_job_id, "extract", (text_out,))
    transport = httpx.MockTransport(lambda _r: httpx.Response(200))
    async with httpx.AsyncClient(transport=transport, base_url="http://stub:8002") as client:
        worker = HttpWorker(
            "http://stub:8002",
            client=client,
            data_dir=tmp_path,
            step_cache=cache,
        )
        job = Job(
            job_id=f"{plan_job_id}-transcribe",
            job_type=WorkerType.ASR,
            payload={"source_language": "en"},
            chapter_id="ch1",
        )
        with pytest.raises(WorkerError, match="no audio file"):
            await worker.execute(job)


@pytest.mark.asyncio
async def test_asr_multipart_missing_audio_file_on_disk(tmp_path: Path, audio_file: Path) -> None:
    """Extract step's audio file is recorded but missing from disk → WorkerError."""
    from acheron.core.errors import WorkerError

    plan_job_id = "job-abc123"
    cache = StepCache(tmp_path)
    await _seed_extract_output(cache, plan_job_id, audio_file)
    # Delete the audio file from disk; the manifest still references it.
    audio_file.unlink()
    transport = httpx.MockTransport(lambda _r: httpx.Response(200))
    async with httpx.AsyncClient(transport=transport, base_url="http://stub:8002") as client:
        worker = HttpWorker(
            "http://stub:8002",
            client=client,
            data_dir=tmp_path,
            step_cache=cache,
        )
        job = Job(
            job_id=f"{plan_job_id}-transcribe",
            job_type=WorkerType.ASR,
            payload={"source_language": "en"},
            chapter_id="ch1",
        )
        with pytest.raises(WorkerError, match="audio file missing"):
            await worker.execute(job)
