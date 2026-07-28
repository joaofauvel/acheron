"""Tests for the Acheron HTTP client."""

from pathlib import Path
from types import TracebackType
from typing import Any

import aiofiles
import aiofiles.base
import httpx
import pytest
import respx
from pydantic import ValidationError

from acheron.api_client import AcheronClient
from acheron.core.schemas import WorkerCapability


@pytest.mark.asyncio
@respx.mock
async def test_submit_job_round_trips_warnings() -> None:
    warning = "BOOTING TTS workers: tts-1 (3s elapsed); cold start typically takes 30\u201390 seconds."
    respx.post("http://test/jobs").mock(
        return_value=httpx.Response(
            201,
            json={"job_id": "job-1", "status": "running", "warnings": [warning]},
        )
    )

    result = await AcheronClient("http://test").submit_job(
        source_type="epub",
        source_path="/input/book.epub",
        source_language="en",
        target_language="es",
    )

    assert result.job_id == "job-1"
    assert result.warnings == [warning]


@pytest.mark.asyncio
@respx.mock
async def test_upload_input_sends_multipart_with_filename_and_bearer_auth(tmp_path: Path) -> None:
    """upload_input streams a local file as multipart with the right filename
    and content, and carries the registration token as a bearer header.
    """
    source = tmp_path / "book.epub"
    source.write_bytes(b"epub-bytes")

    route = respx.post("http://test/inputs").mock(
        return_value=httpx.Response(
            201,
            json={
                "source_path": "inputs/abc/book.epub",
                "filename": "book.epub",
                "size_bytes": len(b"epub-bytes"),
                "content_type": "application/epub+zip",
            },
        )
    )

    result = await AcheronClient("http://test", registration_token="secret").upload_input(source)

    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer secret"
    assert request.headers["content-type"].startswith("multipart/form-data")
    body = request.content
    assert b'filename="book.epub"' in body
    assert b"epub-bytes" in body
    assert result.source_path == "inputs/abc/book.epub"


@pytest.mark.asyncio
@respx.mock
async def test_submit_job_sends_bearer_header_when_token_configured() -> None:
    """submit_job must include the bearer header on the mutation request
    when a registration token is configured on the client.
    """
    route = respx.post("http://test/jobs").mock(
        return_value=httpx.Response(
            201,
            json={"job_id": "job-1", "status": "running"},
        )
    )

    await AcheronClient("http://test", registration_token="secret").submit_job(
        source_type="epub",
        source_path="/input/book.epub",
        source_language="en",
        target_language="es",
    )

    assert route.calls.last.request.headers["authorization"] == "Bearer secret"


@pytest.mark.asyncio
@respx.mock
async def test_get_worker_capabilities_returns_typed_list() -> None:
    """get_worker_capabilities parses the typed capabilities response and
    returns a list of WorkerCapability.
    """
    respx.get("http://test/capabilities", params={"type": "tts"}).mock(
        return_value=httpx.Response(
            200,
            json={
                "language_pairs": [],
                "workers": [
                    {
                        "worker_id": "tts-1",
                        "worker_type": "tts",
                        "model_source": "Qwen/Qwen3-TTS",
                        "metadata": {"voice": "vivian"},
                    }
                ],
            },
        )
    )

    result = await AcheronClient("http://test").get_worker_capabilities("tts")

    assert isinstance(result, list)
    assert all(isinstance(item, WorkerCapability) for item in result)
    assert len(result) == 1
    assert result[0].worker_id == "tts-1"
    assert result[0].worker_type == "tts"


@pytest.mark.asyncio
@respx.mock
async def test_upload_input_reads_file_in_chunks_not_one_shot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """upload_input must read the file in bounded chunks, not in a one-shot full read.

    Regression test for the streaming-upload design requirement: the local
    file is read in ``chunk_size``-byte reads, never as a single
    ``read(-1)`` / no-argument call. We verify the read pattern by wrapping
    ``aiofiles.open`` with a counting file and inspecting the recorded
    read sizes, which is small-file friendly and does not require a
    multi-gigabyte fixture.
    """
    source = tmp_path / "book.epub"
    # 200 bytes of payload; the implementation must still read in chunks.
    source.write_bytes(b"x" * 200)

    respx.post("http://test/inputs").mock(
        return_value=httpx.Response(
            201,
            json={
                "source_path": "inputs/abc/book.epub",
                "filename": "book.epub",
                "size_bytes": 200,
                "content_type": "application/epub+zip",
            },
        )
    )

    read_sizes: list[int] = []

    class _CountingFile:
        """Async file wrapper that records every ``read(size)`` argument."""

        def __init__(self, real_file: Any) -> None:
            self._fp = real_file

        async def read(self, size: int = -1) -> bytes:
            read_sizes.append(size)
            result: bytes = await self._fp.read(size)
            return result

    class _CountingContextManager:
        """Async context manager that hands out a counting file on enter."""

        def __init__(self, real_cm: aiofiles.base.AiofilesContextManager[Any]) -> None:
            self._cm = real_cm

        async def __aenter__(self) -> _CountingFile:
            real_file = await self._cm.__aenter__()
            return _CountingFile(real_file)

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: TracebackType | None,
        ) -> None:
            await self._cm.__aexit__(exc_type, exc, tb)

    def _fake_open(path: Path, mode: str = "rb") -> _CountingContextManager:
        return _CountingContextManager(aiofiles.open(path, mode))  # type: ignore[call-overload]

    class _FakeAiofiles:
        open = staticmethod(_fake_open)

    monkeypatch.setattr("acheron.api_client.aiofiles", _FakeAiofiles)

    await AcheronClient("http://test").upload_input(source)

    # Chunked reads: every read must use a bounded positive size, and the
    # file must be read more than once (so it is not slurped in one shot).
    assert read_sizes, "file was never read"
    assert all(size > 0 for size in read_sizes), f"file was read with unbounded size, not in chunks: {read_sizes}"
    assert len(read_sizes) > 1, f"file was read in a single call: {read_sizes}"


@pytest.mark.asyncio
@respx.mock
async def test_upload_input_falls_back_to_octet_stream_for_unknown_extension(tmp_path: Path) -> None:
    """upload_input must use ``application/octet-stream`` for files whose
    extension has no registered MIME type.
    """
    source = tmp_path / "mystery_blob"
    source.write_bytes(b"raw-bytes")

    route = respx.post("http://test/inputs").mock(
        return_value=httpx.Response(
            201,
            json={
                "source_path": "inputs/abc/mystery_blob",
                "filename": "mystery_blob",
                "size_bytes": len(b"raw-bytes"),
                "content_type": "application/octet-stream",
            },
        )
    )

    result = await AcheronClient("http://test").upload_input(source)

    body = route.calls.last.request.content
    assert b"Content-Type: application/octet-stream" in body
    assert result.source_path == "inputs/abc/mystery_blob"


@pytest.mark.asyncio
@respx.mock
async def test_upload_input_raises_on_invalid_response_body(tmp_path: Path) -> None:
    """upload_input must validate the response body and raise on malformed JSON."""
    source = tmp_path / "book.epub"
    source.write_bytes(b"epub-bytes")

    # Missing required fields (source_path, filename, size_bytes) for InputResponse.
    respx.post("http://test/inputs").mock(
        return_value=httpx.Response(201, json={"unrelated": "field"}),
    )

    with pytest.raises(ValidationError):
        await AcheronClient("http://test").upload_input(source)


@pytest.mark.asyncio
@respx.mock
async def test_resume_job_sends_bearer_header_when_token_configured() -> None:
    """resume_job must include the bearer header on the mutation request
    when a registration token is configured on the client.
    """
    route = respx.post("http://test/jobs/job-1/resume").mock(
        return_value=httpx.Response(
            200,
            json={"job_id": "job-1", "status": "running"},
        )
    )

    await AcheronClient("http://test", registration_token="secret").resume_job("job-1")

    assert route.calls.last.request.headers["authorization"] == "Bearer secret"
