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
                _graphql_response(
                    {"myself": {"endpoints": [{"id": "eid", "gpuIds": "NVIDIA GeForce RTX 3090"}]}}
                ),
                _graphql_response(
                    {"gpuTypes": [{"lowestPrice": {"uninterruptablePrice": 0.69}}]}
                ),
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
                _graphql_response(
                    {"myself": {"endpoints": [{"id": "eid", "gpuIds": "NVIDIA GeForce RTX 3090"}]}}
                ),
                _graphql_response(
                    {"gpuTypes": [{"lowestPrice": {"uninterruptablePrice": 0.69}}]}
                ),
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
        except Exception:
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
            return_value=_graphql_response(
                {"myself": {"endpoints": [{"id": "other-endpoint", "gpuIds": "X"}]}}
            )
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
                _graphql_response(
                    {"myself": {"endpoints": [{"id": "eid", "gpuIds": "X"}]}}
                ),
                _graphql_response({"gpuTypes": []}),
            ]
        )
        price = RunPodPrice(api_key="k", endpoint_id="eid", secure_cloud=False)
        est = await price.estimate(gpu_seconds=10.0)
        assert est.cost is None
