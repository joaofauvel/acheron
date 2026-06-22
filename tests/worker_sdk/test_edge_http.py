"""Tests for the internal edge FastAPI app."""

import httpx
import pytest
from httpx import ASGITransport

from acheron.core.models import Job, WorkerCapabilities, WorkerType
from acheron.worker_sdk._edge_http import EdgeApp
from acheron.worker_sdk.artifacts import BytesArtifact
from acheron.worker_sdk.handler import WorkerHandler


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

    async def handle(self, job: Job) -> list[BytesArtifact]:
        self.calls += 1
        return [BytesArtifact(filename="out.wav", content_type="audio/wav", data=b"audio")]


@pytest.fixture
def app_handler():
    h = _Stub()
    app = EdgeApp(handler=h, capabilities=h.capabilities()).app
    return app, h


class TestEdgeRoutes:
    @pytest.mark.asyncio
    async def test_health_returns_ok(self, app_handler) -> None:
        app, _ = app_handler
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_capabilities_returns_shape(self, app_handler) -> None:
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
    async def test_execute_returns_multipart(self, app_handler) -> None:
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
    async def test_execute_on_handler_error_returns_json(
        self, app_handler, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app, h = app_handler

        async def _boom(job: Job) -> list[BytesArtifact]:
            raise RuntimeError("OOM")

        monkeypatch.setattr(h, "handle", _boom)
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/execute",
                json={"job_id": "j1", "job_type": "tts", "payload": {}, "chapter_id": "ch1"},
            )
        assert r.status_code == 500
        body = r.json()
        assert body["status"] == "failed"
        assert "OOM" in body["error"]
