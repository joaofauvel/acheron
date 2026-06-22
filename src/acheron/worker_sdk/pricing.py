"""Price discovery for Layer 8 workers — fault-tolerant, never blocks a job.

`PriceSource` is the seam. Three variants; workers compose the right one.
The backend calls ``await price_source.estimate(gpu_seconds)`` after each
handle() and populates ``JobMetrics.cost_estimate`` + ``cost_basis`` from
the returned :class:`PriceEstimate`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from acheron.core.models import CostBasis


@dataclass(frozen=True)
class PriceEstimate:
    """Outcome of a price query.

    ``cost is None`` means unknown (provider API unavailable and no cache);
    ``cost == 0.0`` means an actual $0 (stub/local/ZeroPrice).
    """

    cost: float | None
    reason: str | None = None


@runtime_checkable
class PriceSource(Protocol):
    """Provider-agnostic price source."""

    async def estimate(self, gpu_seconds: float) -> PriceEstimate: ...
    async def refresh(self) -> bool: ...


@dataclass(frozen=True)
class ZeroPrice:
    """Stubs/local — no cost tracking. Reports $0 with STATIC basis."""

    async def estimate(self, gpu_seconds: float) -> PriceEstimate:
        return PriceEstimate(cost=0.0, reason="zero (stub/local)")

    async def refresh(self) -> bool:
        return True


@dataclass(frozen=True)
class StaticPrice:
    """Fixed $/hr from config — operator opted out of API rate lookup."""

    dollars_per_hour: float

    async def estimate(self, gpu_seconds: float) -> PriceEstimate:
        cost = round(gpu_seconds * self.dollars_per_hour / 3600.0, 6)
        return PriceEstimate(cost=cost, reason="static config")

    async def refresh(self) -> bool:
        return True


def to_cost_basis(estimate: PriceEstimate) -> CostBasis:
    """Map a :class:`PriceEstimate` to a wire :class:`CostBasis` value.

    RunPodPrice sets ``reason`` to a sentinel string that distinguishes the
    fresh-measurement case from the cached case; the worker-side mapping
    preserves the spec's ``MEASURED`` vs ``CACHED`` distinction.
    """
    if estimate.cost is None:
        return CostBasis.UNKNOWN
    if estimate.reason == "runpod:measured":
        return CostBasis.MEASURED
    if estimate.reason == "runpod:cached":
        return CostBasis.CACHED
    return CostBasis.STATIC
