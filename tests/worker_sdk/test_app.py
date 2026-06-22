"""Tests for create_worker_app factory."""

from typing import Any

import httpx
import pytest
from httpx import ASGITransport

from acheron.core.models import Job, WorkerCapabilities, WorkerType
from acheron.worker_sdk.app import create_worker_app
from acheron.worker_sdk.artifacts import BytesArtifact
from acheron.worker_sdk.handler import WorkerHandler
from acheron.worker_sdk.settings import WorkerSettings


class _Stub(WorkerHandler):
    def capabilities(self) -> WorkerCapabilities:
        return WorkerCapabilities(
            worker_type=WorkerType.TTS,
            supported_languages_in=frozenset({"en"}),
            supported_languages_out=frozenset({"en"}),
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"wav"}),
            max_payload_bytes=None,
            batch_capable=False,
            model_source=None,
        )

    async def handle(self, job: Job) -> list[BytesArtifact]:
        return [BytesArtifact(filename="out.wav", content_type="audio/wav", data=b"audio")]


def _settings(**overrides: Any) -> WorkerSettings:
    base: dict[str, Any] = {
        "worker_id": "w",
        "orchestrator_url": "http://orch:8000",
        "listen_port": 0,
        "price_source": "zero",
    }
    base.update(overrides)
    return WorkerSettings(**base)  # type: ignore[arg-type]


class TestCreateWorkerApp:
    def test_factory_exposes_three_routes(self) -> None:
        h = _Stub()
        s = _settings(price_source="zero")
        app = create_worker_app(handler=h, settings=s, disable_registration=True)
        paths = {r.path for r in app.routes if hasattr(r, "path")}
        assert "/health" in paths
        assert "/capabilities" in paths
        assert "/execute" in paths

    @pytest.mark.asyncio
    async def test_execute_routes_through_app(self) -> None:
        h = _Stub()
        s = _settings(price_source="zero")
        app = create_worker_app(handler=h, settings=s, disable_registration=True)
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/execute",
                json={"job_id": "j1", "job_type": "tts", "payload": {}, "chapter_id": "ch1"},
            )
            assert r.status_code == 200
            assert "multipart/mixed" in r.headers["content-type"]
