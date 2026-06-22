"""Tests for the PriceSource variants (ZeroPrice, StaticPrice)."""

import pytest

from acheron.worker_sdk.pricing import PriceEstimate, StaticPrice, ZeroPrice, to_cost_basis


class TestZeroPrice:
    @pytest.mark.asyncio
    async def test_returns_zero_with_static_label(self) -> None:
        est = await ZeroPrice().estimate(gpu_seconds=10.0)
        assert est.cost == 0.0
        assert to_cost_basis(est).value == "static"


class TestStaticPrice:
    @pytest.mark.asyncio
    async def test_computes_cost_from_rate(self) -> None:
        est = await StaticPrice(dollars_per_hour=0.69).estimate(gpu_seconds=3600.0)
        assert est.cost == 0.69
        assert to_cost_basis(est).value == "static"

    @pytest.mark.asyncio
    async def test_zero_gpu_seconds_yields_zero(self) -> None:
        est = await StaticPrice(dollars_per_hour=0.69).estimate(gpu_seconds=0.0)
        assert est.cost == 0.0
