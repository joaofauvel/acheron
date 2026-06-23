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


class TestToCostBasis:
    @pytest.mark.parametrize(
        ("cost", "reason", "expected"),
        [
            (0.0, "zero (stub/local)", "static"),
            (0.69, "static config", "static"),
            (0.69, "runpod:measured", "measured"),
            (0.69, "runpod:cached", "cached"),
            (None, None, "unknown"),
            (None, "anything goes when cost is None", "unknown"),
        ],
    )
    def test_known_reasons_map_to_wire_value(self, cost: float | None, reason: str | None, expected: str) -> None:
        assert to_cost_basis(PriceEstimate(cost=cost, reason=reason)).value == expected

    def test_raises_on_unknown_reason(self) -> None:
        """New PriceSource implementations must register their reason in
        ``pricing._KNOWN_REASONS``; otherwise we raise rather than silently
        classifying as STATIC.
        """
        with pytest.raises(ValueError, match="bogus"):
            to_cost_basis(PriceEstimate(cost=0.5, reason="bogus"))
