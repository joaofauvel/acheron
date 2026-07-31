"""Tests for structured worker pricing estimates."""

import httpx
import pytest

from acheron.core.models import CostBasis
from acheron.worker_sdk.pricing import (
    PriceEstimate,
    RunPodPrice,
    StaticPrice,
    UnknownPrice,
    ZeroPrice,
    to_cost_basis,
)


class TestZeroPrice:
    @pytest.mark.asyncio
    async def test_returns_zero_with_stub_basis(self) -> None:
        est = await ZeroPrice().estimate(gpu_seconds=10.0)
        assert est.cost == 0.0
        assert est.basis is CostBasis.STUB
        assert to_cost_basis(est) is CostBasis.STUB

    @pytest.mark.asyncio
    async def test_refresh_returns_true(self) -> None:
        assert await ZeroPrice().refresh() is True


class TestStaticPrice:
    @pytest.mark.asyncio
    async def test_computes_cost_from_rate(self) -> None:
        est = await StaticPrice(dollars_per_hour=0.69).estimate(gpu_seconds=3600.0)
        assert est.cost == 0.69
        assert est.basis is CostBasis.STATIC
        assert est.rate_per_hour == 0.69
        assert to_cost_basis(est) is CostBasis.STATIC

    @pytest.mark.parametrize("rate", [-1.0, float("inf"), float("-inf"), float("nan")])
    @pytest.mark.asyncio
    async def test_invalid_rate_is_unknown(self, rate: float) -> None:
        est = await StaticPrice(dollars_per_hour=rate).estimate(gpu_seconds=3600.0)

        assert est.cost is None
        assert est.basis is CostBasis.UNKNOWN

    @pytest.mark.asyncio
    async def test_zero_gpu_seconds_yields_zero(self) -> None:
        est = await StaticPrice(dollars_per_hour=0.69).estimate(gpu_seconds=0.0)
        assert est.cost == 0.0

    @pytest.mark.asyncio
    async def test_refresh_returns_true(self) -> None:
        assert await StaticPrice(dollars_per_hour=0.69).refresh() is True


class TestUnknownPrice:
    @pytest.mark.asyncio
    async def test_returns_unknown_without_zero_cost(self) -> None:
        est = await UnknownPrice().estimate(gpu_seconds=10.0)
        assert est.cost is None
        assert est.basis is CostBasis.UNKNOWN

    @pytest.mark.asyncio
    async def test_refresh_returns_false(self) -> None:
        assert await UnknownPrice().refresh() is False


class TestRunPodPrice:
    @pytest.mark.asyncio
    async def test_reuses_http_client_for_refresh_and_estimate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _FailingClient()
        monkeypatch.setattr(httpx, "AsyncClient", lambda: client)
        price = RunPodPrice(api_key="key", endpoint_id="endpoint")

        assert await price.refresh() is False
        estimate = await price.estimate(gpu_seconds=1.0)
        assert estimate.cost is None
        assert estimate.basis is CostBasis.UNKNOWN
        await price.close()

        assert client.post_calls == 2
        assert client.close_calls == 1


class _FailingClient:
    def __init__(self) -> None:
        self.post_calls = 0
        self.close_calls = 0

    async def post(self, *args: object, **kwargs: object) -> None:
        self.post_calls += 1
        raise httpx.ConnectError("unavailable")

    async def aclose(self) -> None:
        self.close_calls += 1


class TestToCostBasis:
    @pytest.mark.parametrize(
        ("cost", "basis", "expected"),
        [
            (0.0, CostBasis.STUB, CostBasis.STUB),
            (0.69, CostBasis.STATIC, CostBasis.STATIC),
            (0.69, CostBasis.MEASURED, CostBasis.MEASURED),
            (0.69, CostBasis.CACHED, CostBasis.CACHED),
            (None, CostBasis.UNKNOWN, CostBasis.UNKNOWN),
        ],
    )
    def test_explicit_basis_is_preserved(self, cost: float | None, basis: CostBasis, expected: CostBasis) -> None:
        assert to_cost_basis(PriceEstimate(cost=cost, basis=basis)) is expected

    def test_missing_cost_is_always_unknown(self) -> None:
        assert to_cost_basis(PriceEstimate(cost=None, basis=CostBasis.STATIC)) is CostBasis.UNKNOWN

    def test_rejects_invalid_basis(self) -> None:
        with pytest.raises(TypeError, match="basis"):
            to_cost_basis(PriceEstimate(cost=0.5, basis="bogus"))  # type: ignore[arg-type]
