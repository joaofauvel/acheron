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

    async def handle(self, job: Job, input: Input | None = None) -> list[Artifact]:  # noqa: A002
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

    def test_endpoint_url_uses_worker_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When settings.worker_host is set, it is used in the registration endpoint URL."""
        from acheron.worker_sdk.app import _endpoint_url

        monkeypatch.delenv("WORKER_HOST", raising=False)
        s = _settings(price_source="zero", worker_host="edge-prod-1")
        assert _endpoint_url(s) == "http://edge-prod-1:0"

    def test_endpoint_url_defaults_to_localhost(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When worker_host is unset (and WORKER_HOST env unset), the URL uses localhost."""
        from acheron.worker_sdk.app import _endpoint_url

        monkeypatch.delenv("WORKER_HOST", raising=False)
        s = _settings(price_source="zero")
        assert _endpoint_url(s) == "http://localhost:0"


class TestLifespanPriceRefreshExceptionHandling:
    """EXC-004 + OBS-008: price refresh exceptions are narrowed; BaseException subclasses propagate."""

    @pytest.mark.asyncio
    async def test_lifespan_continues_when_price_refresh_raises_httpx_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A transient ``httpx.HTTPError`` from price refresh is logged; the
        lifespan continues so a missing/expired RunPod API key doesn't block
        container startup or registration.
        """
        from acheron.worker_sdk.pricing import RunPodPrice

        monkeypatch.setenv("ACHERON_WORKER__PRICE_SOURCE", "runpod")
        monkeypatch.setenv("ACHERON_WORKER__RUNPOD_API_KEY", "k")
        monkeypatch.setenv("ACHERON_WORKER__RUNPOD_ENDPOINT_ID", "eid123")

        async def _raise(self: RunPodPrice) -> bool:
            raise httpx.HTTPError("boom")

        monkeypatch.setattr(RunPodPrice, "refresh", _raise)
        h = _Stub()
        s = _settings()
        app = create_worker_app(handler=h, settings=s, disable_registration=True)
        with caplog.at_level("WARNING", logger="acheron.worker_sdk.app"):
            async with app.router.lifespan_context(app):
                pass
        assert any("RunPodPrice" in r.message and "price refresh" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_lifespan_propagates_keyboard_interrupt_during_price_refresh(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``KeyboardInterrupt`` during price refresh propagates out of the
        lifespan so a Ctrl-C'd deployer gets a clean shutdown, not a 30s
        hang because the bare ``except BaseException`` swallowed the signal.
        """
        from acheron.worker_sdk.pricing import RunPodPrice

        monkeypatch.setenv("ACHERON_WORKER__PRICE_SOURCE", "runpod")
        monkeypatch.setenv("ACHERON_WORKER__RUNPOD_API_KEY", "k")
        monkeypatch.setenv("ACHERON_WORKER__RUNPOD_ENDPOINT_ID", "eid123")

        async def _raise(self: RunPodPrice) -> bool:
            raise KeyboardInterrupt

        monkeypatch.setattr(RunPodPrice, "refresh", _raise)
        h = _Stub()
        s = _settings()
        app = create_worker_app(handler=h, settings=s, disable_registration=True)
        with pytest.raises(KeyboardInterrupt):
            async with app.router.lifespan_context(app):
                pass
