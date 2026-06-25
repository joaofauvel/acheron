"""Verify the SDK /execute route accepts multipart OR JSON (8b)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from httpx import ASGITransport

from acheron.core.models import Job, WorkerCapabilities, WorkerType
from acheron.worker_sdk._edge_http import EdgeApp
from acheron.worker_sdk.artifacts import Artifact, BytesArtifact
from acheron.worker_sdk.handler import WorkerHandler
from acheron.worker_sdk.inputs import Input


class _AsrEchoHandler(WorkerHandler):
    """ASR handler that records the received input bytes."""

    def __init__(self) -> None:
        self.received: list[bytes] = []
        self.received_content_type: list[str] = []

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            worker_type=WorkerType.ASR,
            supported_languages_in=frozenset({"en"}),
            supported_languages_out=frozenset({"en"}),
            supported_formats_in=frozenset({"mp3", "wav"}),
            supported_formats_out=frozenset({"text"}),
            max_payload_bytes=None,
            batch_capable=False,
            model_source=None,
        )

    async def handle(self, job: Job, input: Input | None = None) -> list[Artifact]:  # noqa: A002
        if input is not None:
            self.received.append(b"".join([c async for c in input.stream()]))
            self.received_content_type.append(input.content_type)
        return [
            BytesArtifact(
                filename="out.txt",
                content_type="text/plain",
                data=b"echoed",
            )
        ]


@pytest.fixture
def app_and_handler() -> Any:
    h = _AsrEchoHandler()
    edge = EdgeApp(handler=h, capabilities=h.capabilities())
    return edge.app, h


class TestJsonRequest:
    @pytest.mark.asyncio
    async def test_json_request_routes_to_legacy_path(self, app_and_handler: Any) -> None:
        app, _ = app_and_handler
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/execute",
                json={
                    "job_id": "j-1",
                    "job_type": "tts",
                    "payload": {},
                    "chapter_id": "ch1",
                    "sequence_ids": None,
                },
            )
        assert resp.status_code == 200
        assert b"echoed" in resp.content


class TestMultipartRequest:
    @pytest.mark.asyncio
    async def test_multipart_request_passes_input(self, app_and_handler: Any) -> None:
        """Multipart with JSON part + audio part → handler receives the audio bytes."""
        app, handler = app_and_handler
        transport = ASGITransport(app=app)
        envelope = json.dumps(
            {
                "job_id": "j-1",
                "job_type": "asr",
                "payload": {"source_language": "en"},
                "chapter_id": "ch1",
                "sequence_ids": None,
            }
        ).encode("utf-8")
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/execute",
                files={
                    "request": ("", envelope, "application/json"),
                    "audio": ("podcast.mp3", b"\xff\xfb\x90\x00mock-audio", "audio/mpeg"),
                },
            )
        assert resp.status_code == 200
        assert b"echoed" in resp.content
        # Handler should have received the audio bytes.
        assert handler.received == [b"\xff\xfb\x90\x00mock-audio"]
        assert handler.received_content_type == ["audio/mpeg"]
        # Response is a valid multipart/mixed body with a trailing application/json metrics part.
        assert "multipart/mixed" in resp.headers["content-type"]

    @pytest.mark.asyncio
    async def test_multipart_request_without_audio_still_works(self, app_and_handler: Any) -> None:
        """Multipart with only the JSON part (no audio) → handler receives input=None."""
        app, handler = app_and_handler
        transport = ASGITransport(app=app)
        envelope = json.dumps(
            {
                "job_id": "j-1",
                "job_type": "asr",
                "payload": {"source_language": "en"},
                "chapter_id": "ch1",
                "sequence_ids": None,
            }
        ).encode("utf-8")
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/execute",
                files={"request": ("", envelope, "application/json")},
            )
        assert resp.status_code == 200
        assert handler.received == []

    @pytest.mark.asyncio
    async def test_multipart_request_missing_json_part_raises(self, app_and_handler: Any) -> None:
        """Multipart with only an audio part (no ``application/json`` part)
        → 500 with a JobResult whose error mentions the missing JSON part."""
        app, _ = app_and_handler
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/execute",
                files={"audio": ("podcast.mp3", b"\xff\xfb\x90\x00", "audio/mpeg")},
            )
        assert resp.status_code == 500
        body = resp.json()
        assert body["status"] == "failed"
        assert "no application/json part" in body["error"]

    @pytest.mark.asyncio
    async def test_multipart_request_missing_boundary_raises(self, app_and_handler: Any) -> None:
        """A multipart Content-Type without a ``boundary=`` parameter → 500
        with a JobResult whose error mentions the missing boundary."""
        app, _ = app_and_handler
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/execute",
                content=b"--notused\r\n",
                headers={"content-type": "multipart/form-data"},
            )
        assert resp.status_code == 500
        body = resp.json()
        assert body["status"] == "failed"
        assert "missing boundary" in body["error"]

    @pytest.mark.asyncio
    async def test_multipart_request_malformed_json_envelope_raises(self, app_and_handler: Any) -> None:
        """CORR-023: an ``application/json`` part whose body is not valid JSON
        must surface as a 500 JobResult failure (sanitised), not a raw stack trace.
        """
        app, _ = app_and_handler
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/execute",
                files={"request": ("", b"not-json", "application/json")},
            )
        assert resp.status_code == 500
        body = resp.json()
        assert body["status"] == "failed"
        assert "Traceback" not in body["error"]
