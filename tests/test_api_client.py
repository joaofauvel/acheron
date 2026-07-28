"""Tests for the Acheron HTTP client."""

from pathlib import Path

import httpx
import pytest
import respx

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
