"""Tests for create_worker_app factory."""

from typing import Any

import httpx
import pytest
import respx
from httpx import ASGITransport

from acheron.core.models import Job, WorkerCapabilities, WorkerType
from acheron.worker_sdk.app import create_worker_app
from acheron.worker_sdk.artifacts import Artifact, BytesArtifact
from acheron.worker_sdk.handler import WorkerHandler
from acheron.worker_sdk.inputs import Input
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

    async def handle(self, job: Job, input: Input | None = None) -> list[Artifact]:
        return [BytesArtifact(filename="out.wav", content_type="audio/wav", data=b"audio")]


def _settings(**overrides: Any) -> WorkerSettings:
    base: dict[str, Any] = {
        "worker_id": "w",
        "orchestrator_url": "http://orch:8000",
        "listen_port": 0,
        "price_source": "zero",
    }
    base.update(overrides)
    return WorkerSettings(**base)


class TestCreateWorkerApp:
    def test_factory_exposes_three_routes(self) -> None:
        h = _Stub()
        s = _settings(price_source="zero")
        app = create_worker_app(handler=h, settings=s, disable_registration=True)
        paths = {getattr(r, "path", "") for r in app.routes}
        assert "/health" in paths
        assert "/capabilities" in paths
        assert "/execute" in paths

    @respx.mock
    @pytest.mark.asyncio
    async def test_registration_payload_includes_runpod_health_metadata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Edge container advertises its RunPod endpoint to the orchestrator's
        RunPodHealthProvider via ``metadata.health_provider`` /
        ``metadata.health_endpoint_id`` (Layer 11 cold-start detection).
        """
        monkeypatch.setenv("ACHERON_WORKER__PRICE_SOURCE", "runpod")
        monkeypatch.setenv("ACHERON_WORKER__RUNPOD_API_KEY", "rk_abc")
        monkeypatch.setenv("ACHERON_WORKER__RUNPOD_ENDPOINT_ID", "eid123")
        # Mock both RunPod GraphQL calls triggered by the lifespan's price refresh.
        respx.post("https://api.runpod.io/graphql").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={"data": {"myself": {"endpoints": [{"id": "eid123", "gpuIds": "NVIDIA GeForce RTX 3090"}]}}},
                ),
                httpx.Response(
                    200,
                    json={"data": {"gpuTypes": [{"lowestPrice": {"uninterruptablePrice": 0.69}}]}},
                ),
            ]
        )
        route = respx.post("http://orch:8000/workers").mock(return_value=httpx.Response(201, json={}))
        h = _Stub()
        s = _settings()
        app = create_worker_app(handler=h, settings=s)
        async with app.router.lifespan_context(app):
            pass
        assert route.called
        sent = route.calls.last.request.content.decode()
        assert '"health_provider":"runpod"' in sent
        assert '"health_endpoint_id":"eid123"' in sent

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
