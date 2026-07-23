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


def _all_paths(routes: object) -> set[str]:
    """Recursively collect the path of every route in a FastAPI app, including
    those nested inside an included APIRouter."""
    paths: set[str] = set()
    for r in routes:  # type: ignore[attr-defined]
        path = getattr(r, "path", None)
        if path:
            paths.add(path)
        # FastAPI wraps included routers in _IncludedRouter; the wrapped
        # APIRouter is reachable via the ``original_router`` attribute.
        nested = getattr(r, "original_router", None)
        if nested is not None and nested is not routes:
            paths.update(_all_paths(nested.routes))
    return paths


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
        # When routes are mounted via APIRouter.include_router, they live on the
        # router, not on app.routes. Walk the included routes recursively.
        paths = _all_paths(app.routes)
        assert "/health" in paths
        assert "/capabilities" in paths
        assert "/execute" in paths

    def test_factory_picks_up_new_edge_routes_automatically(self) -> None:
        """Regression for CORR-015: a new route added to EdgeApp's router
        is reachable on the outer ``create_worker_app`` without copy-paste.
        """

        h = _Stub()
        s = _settings(price_source="zero")
        app = create_worker_app(handler=h, settings=s, disable_registration=True)

        # Find the included edge router (the only non-default _IncludedRouter).
        included = [r for r in app.routes if type(r).__name__ == "_IncludedRouter"]
        assert included, "expected an APIRouter mounted via include_router"
        edge_router = included[0].original_router  # type: ignore[attr-defined]

        @edge_router.get("/version")  # type: ignore[untyped-decorator]
        async def version() -> dict[str, str]:
            return {"version": "test"}

        # New route is now reachable on the outer app.
        paths = _all_paths(app.routes)
        assert "/version" in paths

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
        monkeypatch.delenv("ACHERON_WORKER__WORKER_HOST", raising=False)
        s = _settings(price_source="zero")
        assert _endpoint_url(s) == "http://localhost:0"


class TestBuildPriceSource:
    """TEST-008: cover the ``static`` and ``runpod``-missing-key branches."""

    def test_build_price_source_static_with_rate_returns_static_price(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``price_source='static'`` with a configured ``dollars_per_hour`` returns a
        :class:`StaticPrice` carrying the configured rate."""
        from acheron.worker_sdk.app import _build_price_source
        from acheron.worker_sdk.pricing import StaticPrice

        monkeypatch.setenv("ACHERON_WORKER__PRICE_SOURCE", "static")
        monkeypatch.setenv("ACHERON_WORKER__DOLLARS_PER_HOUR", "1.25")
        s = _settings(price_source="static", dollars_per_hour=1.25)
        source = _build_price_source(s)
        assert isinstance(source, StaticPrice)
        assert source.dollars_per_hour == 1.25

    def test_build_price_source_runpod_without_api_key_returns_zero_stub(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """``price_source='runpod'`` with no API key/endpoint falls back to :class:`ZeroPrice`
        and logs a warning — a worker that forgot to set its env vars must not
        crash the lifespan or break registration."""
        from acheron.worker_sdk.app import _build_price_source
        from acheron.worker_sdk.pricing import ZeroPrice

        monkeypatch.setenv("ACHERON_WORKER__PRICE_SOURCE", "runpod")
        monkeypatch.delenv("ACHERON_WORKER__RUNPOD_API_KEY", raising=False)
        monkeypatch.delenv("ACHERON_WORKER__RUNPOD_ENDPOINT_ID", raising=False)
        s = _settings(price_source="runpod", runpod_api_key=None, runpod_endpoint_id=None)
        with caplog.at_level("WARNING", logger="acheron.worker_sdk.app"):
            source = _build_price_source(s)
        assert isinstance(source, ZeroPrice)
        assert any("RUNPOD_API_KEY" in r.message and "prices will be unknown" in r.message for r in caplog.records)

    def test_build_price_source_static_without_rate_returns_zero_stub(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        from acheron.worker_sdk.app import _build_price_source
        from acheron.worker_sdk.pricing import ZeroPrice

        with caplog.at_level("WARNING", logger="acheron.worker_sdk.app"):
            settings = _settings(price_source="zero").model_copy(
                update={"price_source": "static", "dollars_per_hour": None}
            )
            source = _build_price_source(settings)
        assert isinstance(source, ZeroPrice)
        assert any("dollars_per_hour not set" in r.message for r in caplog.records)

    def test_registration_caps_unchanged_for_non_runpod_source(self) -> None:
        from acheron.worker_sdk.app import _registration_caps

        caps = _Stub().capabilities()
        enriched = _registration_caps(caps, _settings(price_source="zero"))
        assert enriched == caps
        assert "health_provider" not in enriched.metadata
        assert "health_endpoint_id" not in enriched.metadata


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


class TestLifespanCleanup:
    @pytest.mark.asyncio
    async def test_lifespan_closes_price_source_on_normal_shutdown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from acheron.worker_sdk.pricing import RunPodPrice

        monkeypatch.setenv("ACHERON_WORKER__PRICE_SOURCE", "runpod")
        monkeypatch.setenv("ACHERON_WORKER__RUNPOD_API_KEY", "k")
        monkeypatch.setenv("ACHERON_WORKER__RUNPOD_ENDPOINT_ID", "eid123")
        close_calls = 0

        async def _refresh(self: RunPodPrice) -> bool:
            return True

        async def _close(self: RunPodPrice) -> None:
            nonlocal close_calls
            close_calls += 1

        monkeypatch.setattr(RunPodPrice, "refresh", _refresh)
        monkeypatch.setattr(RunPodPrice, "close", _close)
        app = create_worker_app(handler=_Stub(), settings=_settings(), disable_registration=True)

        async with app.router.lifespan_context(app):
            pass

        assert close_calls == 1

    @pytest.mark.asyncio
    async def test_lifespan_closes_price_source_when_handler_shutdown_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from acheron.worker_sdk.pricing import RunPodPrice

        monkeypatch.setenv("ACHERON_WORKER__PRICE_SOURCE", "runpod")
        monkeypatch.setenv("ACHERON_WORKER__RUNPOD_API_KEY", "k")
        monkeypatch.setenv("ACHERON_WORKER__RUNPOD_ENDPOINT_ID", "eid123")
        close_calls = 0

        async def _refresh(self: RunPodPrice) -> bool:
            return True

        async def _close(self: RunPodPrice) -> None:
            nonlocal close_calls
            close_calls += 1

        class _FailingShutdownHandler(_Stub):
            async def shutdown(self) -> None:
                msg = "handler shutdown failed"
                raise RuntimeError(msg)

        monkeypatch.setattr(RunPodPrice, "refresh", _refresh)
        monkeypatch.setattr(RunPodPrice, "close", _close)
        app = create_worker_app(
            handler=_FailingShutdownHandler(),
            settings=_settings(),
            disable_registration=True,
        )

        with pytest.raises(RuntimeError, match="handler shutdown failed"):
            async with app.router.lifespan_context(app):
                pass

        assert close_calls == 1
