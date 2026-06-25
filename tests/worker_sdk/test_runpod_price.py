"""Tests for the RunPodPrice variant."""

import httpx
import pytest
import respx

from acheron.worker_sdk.pricing import RunPodPrice, to_cost_basis


def _graphql_response(data: dict[str, object]) -> httpx.Response:
    return httpx.Response(200, json={"data": data})


class TestRunPodPrice:
    @respx.mock
    @pytest.mark.asyncio
    async def test_measured_when_fresh_refresh_succeeds(self) -> None:
        routes = respx.post("https://api.runpod.io/graphql")
        routes.mock(
            side_effect=[
                _graphql_response({"myself": {"endpoints": [{"id": "eid", "gpuIds": "NVIDIA GeForce RTX 3090"}]}}),
                _graphql_response({"gpuTypes": [{"lowestPrice": {"uninterruptablePrice": 0.69}}]}),
            ]
        )
        price = RunPodPrice(api_key="k", endpoint_id="eid", secure_cloud=False, cache_ttl_s=3600.0)
        est = await price.estimate(gpu_seconds=3600.0)
        assert est.cost == 0.69
        assert est.reason == "runpod:measured"
        assert to_cost_basis(est).value == "measured"

    @respx.mock
    @pytest.mark.asyncio
    async def test_cached_when_refresh_fails_after_a_prior_success(self) -> None:
        routes = respx.post("https://api.runpod.io/graphql")
        routes.mock(
            side_effect=[
                _graphql_response({"myself": {"endpoints": [{"id": "eid", "gpuIds": "NVIDIA GeForce RTX 3090"}]}}),
                _graphql_response({"gpuTypes": [{"lowestPrice": {"uninterruptablePrice": 0.69}}]}),
                httpx.ConnectError("boom"),
                httpx.ConnectError("boom"),
            ]
        )
        price = RunPodPrice(api_key="k", endpoint_id="eid", secure_cloud=False, cache_ttl_s=0.0)
        est1 = await price.estimate(gpu_seconds=3600.0)
        assert est1.reason == "runpod:measured"
        est2 = await price.estimate(gpu_seconds=3600.0)
        assert est2.cost == 0.69
        assert est2.reason == "runpod:cached"
        assert to_cost_basis(est2).value == "cached"

    @respx.mock
    @pytest.mark.asyncio
    async def test_unknown_when_never_refreshed(self) -> None:
        respx.post("https://api.runpod.io/graphql").mock(side_effect=httpx.ConnectError("boom"))
        price = RunPodPrice(api_key="k", endpoint_id="eid", secure_cloud=False)
        est = await price.estimate(gpu_seconds=3600.0)
        assert est.cost is None
        assert "unavailable" in (est.reason or "")
        assert to_cost_basis(est).value == "unknown"

    @respx.mock
    @pytest.mark.asyncio
    async def test_api_failure_does_not_propagate(self) -> None:
        respx.post("https://api.runpod.io/graphql").mock(return_value=httpx.Response(500))
        price = RunPodPrice(api_key="k", endpoint_id="eid", secure_cloud=False)
        try:
            est = await price.estimate(gpu_seconds=3600.0)
        except httpx.HTTPError:
            pytest.fail("RunPodPrice.estimate must not raise on API failure")
        assert est.cost is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_unknown_when_configured_endpoint_id_missing(self) -> None:
        """The GraphQL response lists endpoints, but the configured
        ``endpoint_id`` is not among them — refresh fails, caller sees
        ``cost=None`` with the "unavailable" reason.
        """
        respx.post("https://api.runpod.io/graphql").mock(
            return_value=_graphql_response({"myself": {"endpoints": [{"id": "other-endpoint", "gpuIds": "X"}]}})
        )
        price = RunPodPrice(api_key="k", endpoint_id="missing", secure_cloud=False)
        est = await price.estimate(gpu_seconds=10.0)
        assert est.cost is None
        assert "unavailable" in (est.reason or "")

    @respx.mock
    @pytest.mark.asyncio
    async def test_unknown_when_myself_endpoints_null_or_empty(self) -> None:
        """Guard against the GraphQL response shape ``myself.endpoints = null``
        or ``[]`` — the worker must not crash on either.
        """
        respx.post("https://api.runpod.io/graphql").mock(
            return_value=_graphql_response({"myself": {"endpoints": None}})
        )
        price = RunPodPrice(api_key="k", endpoint_id="eid", secure_cloud=False)
        est = await price.estimate(gpu_seconds=10.0)
        assert est.cost is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_unknown_when_gpu_types_null_or_empty(self) -> None:
        """Guard against ``gpuTypes = null | []`` after a successful endpoint
        lookup — refresh fails, caller sees ``cost=None``.
        """
        respx.post("https://api.runpod.io/graphql").mock(
            side_effect=[
                _graphql_response({"myself": {"endpoints": [{"id": "eid", "gpuIds": "X"}]}}),
                _graphql_response({"gpuTypes": []}),
            ]
        )
        price = RunPodPrice(api_key="k", endpoint_id="eid", secure_cloud=False)
        est = await price.estimate(gpu_seconds=10.0)
        assert est.cost is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_refresh_failure_logs_endpoint_id_and_exception_class(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """OBS-006: on transport failure, the pricing module emits a log line
        naming the ``endpoint_id`` and the exception class so an operator can
        diagnose a permanently-broken rate lookup from the cache silence.
        """
        import logging

        respx.post("https://api.runpod.io/graphql").mock(side_effect=httpx.ConnectError("boom"))
        price = RunPodPrice(api_key="k", endpoint_id="eid-bad", secure_cloud=False)
        with caplog.at_level(logging.ERROR, logger="acheron.worker_sdk.pricing"):
            est = await price.estimate(gpu_seconds=10.0)
        assert est.cost is None
        assert any("eid-bad" in r.message and "ConnectError" in r.message for r in caplog.records), (
            f"expected log with endpoint_id+exc_class, got: {[r.message for r in caplog.records]}"
        )

    @respx.mock
    @pytest.mark.asyncio
    async def test_api_key_sent_as_authorization_header_not_query_param(self) -> None:
        """SEC-013: the RunPod API key must travel in the ``Authorization``
        Bearer header, not as a URL query parameter — query strings are
        routinely logged by HTTP access middleware, CDN edges, and proxy
        layers (CWE-598 / OWASP API3:2023).
        """
        route = respx.post("https://api.runpod.io/graphql").mock(return_value=_graphql_response({}))
        price = RunPodPrice(api_key="rk_SECRET_DO_NOT_LOG", endpoint_id="eid", secure_cloud=False)
        await price.refresh()
        request = route.calls.last.request
        assert "api_key" not in request.url.params
        assert "rk_SECRET_DO_NOT_LOG" not in str(request.url)
        assert request.headers["authorization"] == "Bearer rk_SECRET_DO_NOT_LOG"
