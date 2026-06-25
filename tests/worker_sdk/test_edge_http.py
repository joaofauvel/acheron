"""Tests for the internal edge FastAPI app."""

from typing import TYPE_CHECKING

import httpx
import pytest
from httpx import ASGITransport

from acheron.core.models import Job, WorkerCapabilities, WorkerType
from acheron.worker_sdk._edge_http import EdgeApp
from acheron.worker_sdk.artifacts import Artifact, BytesArtifact
from acheron.worker_sdk.handler import WorkerHandler
from acheron.worker_sdk.inputs import Input

if TYPE_CHECKING:
    from fastapi import FastAPI


class _Stub(WorkerHandler):
    def __init__(self) -> None:
        self.calls = 0

    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            worker_type=WorkerType.TTS,
            supported_languages_in=frozenset({"en"}),
            supported_languages_out=frozenset({"en"}),
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"wav"}),
            max_payload_bytes=None,
            batch_capable=False,
            model_source="huggingface:test",
        )

    async def handle(self, job: Job, input: Input | None = None) -> list[Artifact]:  # noqa: A002
        self.calls += 1
        return [BytesArtifact(filename="out.wav", content_type="audio/wav", data=b"audio")]


@pytest.fixture
def app_handler() -> tuple[FastAPI, _Stub]:
    h = _Stub()
    app = EdgeApp(handler=h, capabilities=h.capabilities()).app
    return app, h


class TestEdgeRoutes:
    @pytest.mark.asyncio
    async def test_health_returns_ok(self, app_handler: tuple[FastAPI, _Stub]) -> None:
        app, _ = app_handler
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_capabilities_returns_shape(self, app_handler: tuple[FastAPI, _Stub]) -> None:
        app, _ = app_handler
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/capabilities")
        assert r.status_code == 200
        body = r.json()
        assert body["worker_type"] == "tts"
        assert body["supported_languages_in"] == ["en"]
        assert body["supported_formats_out"] == ["wav"]

    @pytest.mark.asyncio
    async def test_execute_returns_multipart(self, app_handler: tuple[FastAPI, _Stub]) -> None:
        app, h = app_handler
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/execute",
                json={
                    "job_id": "j1",
                    "job_type": "tts",
                    "payload": {"chunks": [{"text": "hi"}], "target_language": "en"},
                    "chapter_id": "ch1",
                },
            )
        assert r.status_code == 200
        assert "multipart/mixed" in r.headers["content-type"]
        assert h.calls == 1
        assert b"audio" in r.content

    @pytest.mark.asyncio
    async def test_execute_on_handler_error_returns_jobresult_json(
        self,
        app_handler: tuple[FastAPI, _Stub],
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """On handler error, the body is a ``JobResult`` JSON (status=failed,
        job_id echoed, error populated, no outputs) so the orchestrator's
        :class:`TypeAdapter(JobResult).validate_json` parser succeeds. The
        ``error`` field is sanitised to ``"<ClassName>: <first line>"`` so
        internal exception detail (file paths, secrets) does not leak back
        to the orchestrator.
        """
        app, h = app_handler

        async def _boom(job: Job, input: Input | None = None) -> list[BytesArtifact]:  # noqa: A002
            raise RuntimeError("OOM")

        monkeypatch.setattr(h, "handle", _boom)
        transport = ASGITransport(app=app)
        with caplog.at_level("ERROR", logger="acheron.worker_sdk._edge_http"):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                r = await c.post(
                    "/execute",
                    json={"job_id": "j1", "job_type": "tts", "payload": {}, "chapter_id": "ch1"},
                )
        assert r.status_code == 500
        body = r.json()
        assert body["job_id"] == "j1"
        assert body["status"] == "failed"
        assert body["outputs"] == []
        assert body["error"] == "RuntimeError: OOM"
        assert body["metrics"]["duration_seconds"] >= 0.0
        assert body["metrics"]["cost_basis"] is None
        assert any("handler failed" in r.message and "_Stub" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_execute_error_sanitises_secrets_in_message(
        self,
        app_handler: tuple[FastAPI, _Stub],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """SEC-012: a handler exception whose message contains a credential
        pattern must not surface that pattern in the 500 body. The full
        message is preserved in ``logger.exception`` for the operator.
        """
        app, h = app_handler

        async def _boom(job: Job, input: Input | None = None) -> list[BytesArtifact]:  # noqa: A002
            msg = "DB connect failed password=foo at /runpod-volume/models/qwen3"
            raise RuntimeError(msg)

        monkeypatch.setattr(h, "handle", _boom)
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/execute",
                json={"job_id": "j1", "job_type": "tts", "payload": {}, "chapter_id": "ch1"},
            )
        assert r.status_code == 500
        body = r.json()
        assert "password=foo" not in body["error"]
        assert body["error"].startswith("RuntimeError:")

    @pytest.mark.asyncio
    async def test_dispatch_propagates_keyboard_interrupt(
        self,
        app_handler: tuple[FastAPI, _Stub],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``KeyboardInterrupt`` from the handler propagates out of the
        ``/execute`` route (and ``_dispatch``) rather than being wrapped in a
        500 — the operator's Ctrl-C during a long handler must reach uvicorn's
        signal handler rather than being logged as a normal job failure.
        """
        app, h = app_handler

        async def _interrupt(job: Job, input: Input | None = None) -> list[BytesArtifact]:  # noqa: A002
            raise KeyboardInterrupt

        monkeypatch.setattr(h, "handle", _interrupt)
        transport = ASGITransport(app=app, raise_app_exceptions=True)
        with pytest.raises(KeyboardInterrupt):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                await c.post(
                    "/execute",
                    json={"job_id": "j1", "job_type": "tts", "payload": {}, "chapter_id": "ch1"},
                )

    @pytest.mark.asyncio
    async def test_execute_metrics_part_emits_null_cost_basis(self, app_handler: tuple[FastAPI, _Stub]) -> None:
        """When no price source is wired, the metrics part emits ``"cost_basis": null``
        (not the string ``"unknown"``) — the latter would conflate "no estimate"
        with "the API was down", breaking the dashboard's cost-confidence render.
        """
        import json

        app, _ = app_handler
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/execute",
                json={
                    "job_id": "j1",
                    "job_type": "tts",
                    "payload": {"chunks": [{"text": "hi"}]},
                    "chapter_id": "ch1",
                },
            )
        assert r.status_code == 200
        # Pull the last part (application/json metrics) out of the multipart body.
        body = r.content
        boundary = r.headers["content-type"].split("boundary=")[-1]
        parts = body.split(f"--{boundary}".encode())
        json_part = next(p for p in parts if b"application/json" in p)
        # The JSON payload begins after the headers (blank line) and ends before \r\n.
        json_bytes = json_part.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n", 1)[0]
        metrics = json.loads(json_bytes)
        assert metrics["cost_basis"] is None
        assert "unknown" not in json_bytes.decode("utf-8")

    @pytest.mark.asyncio
    async def test_execute_response_carries_x_acheron_metadata_per_artifact(self) -> None:
        """TEST-013: each artifact in the multipart response must carry an
        ``X-Acheron-Metadata`` header whose value is the JSON-serialized
        ``artifact.metadata`` dict — the build-side mirror of the request-side
        parser (CORR-013)."""
        import json

        class _MetaStub(WorkerHandler):
            def capabilities(self) -> WorkerCapabilities:
                return WorkerCapabilities(
                    worker_type=WorkerType.TTS,
                    supported_languages_in=frozenset({"en"}),
                    supported_languages_out=frozenset({"en"}),
                    supported_formats_in=frozenset({"text"}),
                    supported_formats_out=frozenset({"wav"}),
                    max_payload_bytes=None,
                    batch_capable=False,
                    model_source="huggingface:test",
                )

            async def handle(self, job: Job, input: Input | None = None) -> list[Artifact]:  # noqa: A002
                return [
                    BytesArtifact(
                        filename="out.wav",
                        content_type="audio/wav",
                        data=b"audio",
                        metadata={"sequence_id": 0, "chapter_id": "ch1"},
                    )
                ]

        h = _MetaStub()
        app = EdgeApp(handler=h, capabilities=h.capabilities()).app
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/execute",
                json={
                    "job_id": "j1",
                    "job_type": "tts",
                    "payload": {"chunks": [{"text": "hi"}]},
                    "chapter_id": "ch1",
                },
            )
        assert r.status_code == 200
        # Find the audio part and parse its X-Acheron-Metadata header.
        body = r.content
        boundary = r.headers["content-type"].split("boundary=")[-1]
        audio_part = next(p for p in body.split(f"--{boundary}".encode()) if b"audio/wav" in p)
        header_block = audio_part.split(b"\r\n\r\n", 1)[0].decode("utf-8")
        meta_line = next(line for line in header_block.split("\r\n") if line.startswith("X-Acheron-Metadata:"))
        payload = meta_line.split(":", 1)[1].strip()
        assert json.loads(payload) == {"sequence_id": 0, "chapter_id": "ch1"}


class TestEdgeExecuteAuth:
    """OBS-010: /execute must require a Bearer token when registration_token is configured."""

    @pytest.fixture
    def app_with_token(self) -> tuple[FastAPI, _Stub]:
        h = _Stub()
        app = EdgeApp(handler=h, capabilities=h.capabilities(), registration_token="test-secret-32-chars-min-aaaa").app
        return app, h

    @pytest.mark.asyncio
    async def test_execute_rejects_missing_authorization_header(self, app_with_token: tuple[FastAPI, _Stub]) -> None:
        app, h = app_with_token
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/execute",
                json={"job_id": "j1", "job_type": "tts", "payload": {}, "chapter_id": "ch1"},
            )
        assert r.status_code == 401
        assert h.calls == 0

    @pytest.mark.asyncio
    async def test_execute_rejects_wrong_authorization_token(self, app_with_token: tuple[FastAPI, _Stub]) -> None:
        app, h = app_with_token
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/execute",
                headers={"Authorization": "Bearer wrong-token"},
                json={"job_id": "j1", "job_type": "tts", "payload": {}, "chapter_id": "ch1"},
            )
        assert r.status_code == 401
        assert h.calls == 0

    @pytest.mark.asyncio
    async def test_execute_accepts_correct_bearer_token(self, app_with_token: tuple[FastAPI, _Stub]) -> None:
        app, h = app_with_token
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/execute",
                headers={"Authorization": "Bearer test-secret-32-chars-min-aaaa"},
                json={"job_id": "j1", "job_type": "tts", "payload": {}, "chapter_id": "ch1"},
            )
        assert r.status_code == 200
        assert h.calls == 1

    @pytest.mark.asyncio
    async def test_execute_open_mode_when_token_unset(self, app_handler: tuple[FastAPI, _Stub]) -> None:
        """When registration_token is None, /execute must remain open (back-compat with existing tests)."""
        app, h = app_handler
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/execute",
                json={"job_id": "j1", "job_type": "tts", "payload": {}, "chapter_id": "ch1"},
            )
        assert r.status_code == 200
        assert h.calls == 1

    @pytest.mark.asyncio
    async def test_health_and_capabilities_remain_unauthenticated(self, app_with_token: tuple[FastAPI, _Stub]) -> None:
        app, _ = app_with_token
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            h = await c.get("/health")
            caps = await c.get("/capabilities")
        assert h.status_code == 200
        assert caps.status_code == 200
