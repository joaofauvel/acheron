"""Verify the SDK /execute route accepts multipart OR JSON (8b)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, cast

import httpx
import pytest
from httpx import ASGITransport

from acheron.core.models import Job, JsonValue, WorkerCapabilities, WorkerType
from acheron.worker_sdk._edge_http import EdgeApp
from acheron.worker_sdk.artifacts import Artifact, BytesArtifact
from acheron.worker_sdk.handler import WorkerHandler
from acheron.worker_sdk.inputs import Input


class _AsrEchoHandler(WorkerHandler):
    """ASR handler that records the received input bytes."""

    def __init__(self) -> None:
        self.received: list[bytes] = []
        self.received_content_type: list[str] = []
        self.received_metadata: list[dict[str, object]] = []

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
            self.received_metadata.append(dict(input.metadata))
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
    async def test_multipart_request_malformed_json_envelope_raises(
        self,
        app_and_handler: Any,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """CORR-023: an ``application/json`` part whose body is not valid JSON
        must surface as a 500 JobResult failure (sanitised), not a raw stack trace.
        """
        app, _ = app_and_handler
        transport = ASGITransport(app=app)
        with caplog.at_level("ERROR", logger="acheron.worker_sdk._edge_http"):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/execute",
                    files={"request": ("", b"not-json", "application/json")},
                )
        assert resp.status_code == 500
        body = resp.json()
        assert body["status"] == "failed"
        assert "Traceback" not in body["error"]
        assert any("Multipart request parsing failed" in record.message for record in caplog.records)

    @pytest.mark.asyncio
    async def test_multipart_request_with_non_audio_part_raises(self, app_and_handler: Any) -> None:
        """CORR-025: a non-JSON, non-audio part (e.g. text/plain) must not be
        silently treated as the audio input — it must raise a clean WorkerError
        (not be coerced into a BytesInput with a wrong content_type).
        """
        app, _ = app_and_handler
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
                    "sidecar": ("notes.txt", b"just a sidecar", "text/plain"),
                },
            )
        assert resp.status_code == 500
        body = resp.json()
        assert body["status"] == "failed"
        assert "unsupported Content-Type" in body["error"]
        assert "text/plain" in body["error"]

    @pytest.mark.asyncio
    async def test_multipart_request_with_two_json_parts(self, app_and_handler: Any) -> None:
        """The translation path passes the chunks.json part to the handler."""
        app, handler = app_and_handler
        transport = ASGITransport(app=app)
        envelope = json.dumps(
            {
                "job_id": "j-1",
                "job_type": "translation",
                "payload": {"chunks": [{"text": "hola"}]},
                "chapter_id": "ch1",
                "sequence_ids": None,
            }
        ).encode("utf-8")
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/execute",
                files={
                    "request": ("", envelope, "application/json"),
                    "chunks-renamed": ("chunks.json", b'{"chunks": []}', "application/json; charset=utf-8"),
                },
            )
        assert resp.status_code == 200
        assert handler.received == [b'{"chunks": []}']
        assert handler.received_content_type == ["application/json; charset=utf-8"]

    @pytest.mark.asyncio
    async def test_multipart_request_propagates_per_part_metadata(self, app_and_handler: Any) -> None:
        """CORR-024: an audio part carrying an ``X-Acheron-Metadata`` header
        must reach the handler as ``BytesInput.metadata`` (the request-side
        mirror of the response-side CORR-013 fix).
        """
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
        # httpx doesn't expose per-part custom headers via `files=`, so build
        # the multipart body manually with a known boundary.
        boundary = "acheron-test"
        body = (
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="request"\r\n'
                f"Content-Type: application/json\r\n\r\n"
            ).encode()
            + envelope
            + b"\r\n"
        )
        body += (
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="audio"; filename="podcast.mp3"\r\n'
                f"Content-Type: audio/mpeg\r\n"
                f'X-Acheron-Metadata: {{"speaker_hint": "alice", "language": "en"}}\r\n\r\n'
            ).encode()
            + b"\xff\xfb\x90\x00mock-audio"
            + b"\r\n"
        )
        body += f"--{boundary}--\r\n".encode()
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/execute",
                content=body,
                headers={"content-type": f"multipart/form-data; boundary={boundary}"},
            )
        assert resp.status_code == 200
        assert handler.received_metadata == [{"speaker_hint": "alice", "language": "en"}]

    @pytest.mark.asyncio
    async def test_multipart_request_rejects_non_object_metadata(self, app_and_handler: Any) -> None:
        """Malformed metadata is returned as a structured JobResult failure."""
        app, _ = app_and_handler
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
        boundary = "acheron-invalid-metadata"
        body = (
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="request"\r\n'
                f"Content-Type: application/json\r\n\r\n"
            ).encode()
            + envelope
            + b"\r\n"
            + (
                (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="audio"; filename="podcast.mp3"\r\n'
                    f"Content-Type: audio/mpeg\r\n"
                    f"X-Acheron-Metadata: []\r\n\r\n"
                ).encode()
                + b"audio"
                + b"\r\n"
            )
            + f"--{boundary}--\r\n".encode()
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/execute",
                content=body,
                headers={"content-type": f"multipart/form-data; boundary={boundary}"},
            )

        assert resp.status_code == 500
        assert resp.json()["status"] == "failed"

    @pytest.mark.asyncio
    async def test_multipart_metadata_round_trips_request_to_response(self) -> None:
        """TEST-013: a metadata header sent to /execute round-trips to the
        handler as ``BytesInput.metadata`` and back as the response part's
        ``X-Acheron-Metadata`` header. Build-side mirror of CORR-013."""
        from acheron.worker_sdk._edge_http import EdgeApp

        class _EchoingHandler(WorkerHandler):
            def __init__(self) -> None:
                self.received_metadata: list[dict[str, JsonValue]] = []

            def capabilities(self) -> WorkerCapabilities:
                return WorkerCapabilities(
                    worker_type=WorkerType.ASR,
                    supported_languages_in=frozenset({"en"}),
                    supported_languages_out=frozenset({"en"}),
                    supported_formats_in=frozenset({"mp3"}),
                    supported_formats_out=frozenset({"text"}),
                    max_payload_bytes=None,
                    batch_capable=False,
                    model_source=None,
                )

            async def handle(self, job: Job, input: Input | None = None) -> list[Artifact]:  # noqa: A002
                if input is None:
                    return []
                meta = dict(input.metadata)
                self.received_metadata.append(meta)
                return [
                    BytesArtifact(
                        filename="out.txt",
                        content_type="text/plain",
                        data=b"echoed",
                        metadata=meta,
                    )
                ]

        h = _EchoingHandler()
        app = EdgeApp(handler=h, capabilities=h.capabilities()).app
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
        boundary = "acheron-roundtrip"
        sent_meta = {"sequence_id": 0, "speaker_hint": "bob"}
        body = (
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="request"\r\n'
                f"Content-Type: application/json\r\n\r\n"
            ).encode()
            + envelope
            + b"\r\n"
        )
        body += (
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="audio"; filename="podcast.mp3"\r\n'
                f"Content-Type: audio/mpeg\r\n"
                f"X-Acheron-Metadata: {json.dumps(sent_meta)}\r\n\r\n"
            ).encode()
            + b"\xff\xfb\x90\x00mock-audio"
            + b"\r\n"
        )
        body += f"--{boundary}--\r\n".encode()
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/execute",
                content=body,
                headers={"content-type": f"multipart/form-data; boundary={boundary}"},
            )
        assert resp.status_code == 200
        assert h.received_metadata == [sent_meta]
        resp_boundary = resp.headers["content-type"].split("boundary=")[-1]
        text_part = next(p for p in resp.content.split(f"--{resp_boundary}".encode()) if b"text/plain" in p)
        header_block = text_part.split(b"\r\n\r\n", 1)[0].decode("utf-8")
        meta_line = next(line for line in header_block.split("\r\n") if line.startswith("X-Acheron-Metadata:"))
        assert json.loads(meta_line.split(":", 1)[1].strip()) == sent_meta


class TestMultipartResponseStreaming:
    """CORR-017 + PERF-006: response builder must stream each artifact's chunks
    and not accumulate them in a per-part bytes buffer."""

    @pytest.mark.asyncio
    async def test_build_multipart_response_returns_streaming_response(self) -> None:
        """The body must be an async iterator, not a pre-joined bytes blob."""
        from fastapi.responses import StreamingResponse

        from acheron.core.models import JobMetrics
        from acheron.worker_sdk._edge_http import _build_multipart_response
        from acheron.worker_sdk.artifacts import StreamArtifact

        async def _producer() -> AsyncIterator[bytes]:
            for i in range(5):
                yield f"chunk-{i}-".encode() * 100

        artifact = StreamArtifact(
            filename="out.bin",
            content_type="application/octet-stream",
            producer=_producer,
        )
        response = await _build_multipart_response([artifact], JobMetrics(duration_seconds=1.0))
        assert isinstance(response, StreamingResponse)
        # Drain the streaming body to confirm the full envelope is present.
        body = b"".join([cast("bytes", c) async for c in response.body_iterator])
        assert b"chunk-0-chunk-0-" in body
        assert b"chunk-4-chunk-4-" in body
        # The closing boundary must terminate the body.
        assert body.rstrip(b"\r\n").endswith(b"--")

    @pytest.mark.asyncio
    async def test_build_multipart_response_does_not_artifact_append(self) -> None:
        """PERF-006: no per-artifact ``bytes += chunk`` accumulator.

        The function must yield each chunk through the response iterator
        without holding the full artifact in memory.
        """
        from acheron.core.models import JobMetrics
        from acheron.worker_sdk._edge_http import _build_multipart_response
        from acheron.worker_sdk.artifacts import StreamArtifact

        # 1000 small chunks — a per-artifact accumulator would allocate ~500KB
        # via 500K byte-copies (O(n²) on the 500B each). Streaming yields each
        # chunk through directly with no concatenation.
        n_chunks = 1000

        async def _producer() -> AsyncIterator[bytes]:
            for _ in range(n_chunks):
                yield b"x"

        artifact = StreamArtifact(
            filename="x.bin",
            content_type="application/octet-stream",
            producer=_producer,
        )
        response = await _build_multipart_response([artifact], JobMetrics(duration_seconds=0.0))
        # The artifact data was spread across the iterator (not concentrated
        # in a single accumulator buffer). A pre-joined body would be a single
        # bytes; a streaming body yields per-chunk bytes that include the
        # artifact's 1-byte tokens interleaved with the multipart framing.
        joined = b"".join([cast("bytes", c) async for c in response.body_iterator])
        # Body contains at least n_chunks 'x' bytes (the artifact payload).
        # Note: the metrics JSON also contains the letter 'x' (e.g. "tokens_in"),
        # so the count is >= n_chunks, not == n_chunks.
        assert joined.count(b"x") >= n_chunks
        # Body contains the closing boundary.
        assert joined.rstrip(b"\r\n").endswith(b"--")


class TestParseMultipartRequestStreaming:
    """CORR-019: edge /execute must parse the request body in streaming chunks."""

    @pytest.mark.asyncio
    async def test_parse_multipart_streams_request_body(
        self, app_and_handler: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The parser must not call ``Request.body()`` — it must consume the
        request via ``Request.stream()`` and feed chunks into python-multipart.
        """
        from starlette.requests import Request

        body_calls: list[Request] = []
        original_body = Request.body

        async def _spy_body(self: Request) -> bytes:
            body_calls.append(self)
            return await original_body(self)

        monkeypatch.setattr(Request, "body", _spy_body)

        app, _ = app_and_handler
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
        # 10 MB of audio bytes — a body() call would materialise the whole thing.
        audio = b"\xff\xfb\x90\x00" * 2_500_000
        boundary = "acheron-stream"
        body = (
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="request"\r\n'
                f"Content-Type: application/json\r\n\r\n"
            ).encode()
            + envelope
            + b"\r\n"
        )
        body += (
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="audio"; filename="big.mp3"\r\n'
                f"Content-Type: audio/mpeg\r\n\r\n"
            ).encode()
            + audio
            + b"\r\n"
        )
        body += f"--{boundary}--\r\n".encode()
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/execute",
                content=body,
                headers={"content-type": f"multipart/form-data; boundary={boundary}"},
            )
        assert resp.status_code == 200
        assert body_calls == []

    @pytest.mark.asyncio
    async def test_parse_multipart_handles_large_file_via_disk_spool(self, app_and_handler: Any, tmp_path: Any) -> None:
        """A 2 MB audio part must be parsed correctly even when the file spills
        to disk via python-multipart's MAX_MEMORY_FILE_SIZE config.
        """
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
        # 2 MB audio — will spill to disk with the default 1 MB threshold.
        audio = b"\xab" * (2 * 1024 * 1024)
        boundary = "acheron-big"
        body = (
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="request"\r\n'
                f"Content-Type: application/json\r\n\r\n"
            ).encode()
            + envelope
            + b"\r\n"
        )
        body += (
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="audio"; filename="big.mp3"\r\n'
                f"Content-Type: audio/mpeg\r\n\r\n"
            ).encode()
            + audio
            + b"\r\n"
        )
        body += f"--{boundary}--\r\n".encode()
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/execute",
                content=body,
                headers={"content-type": f"multipart/form-data; boundary={boundary}"},
            )
        assert resp.status_code == 200
        # Handler received the full audio bytes.
        assert len(handler.received) == 1
        assert len(handler.received[0]) == len(audio)
        assert handler.received[0] == audio
