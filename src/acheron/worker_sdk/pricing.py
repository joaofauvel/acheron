"""Price discovery for Layer 8 workers — fault-tolerant, never blocks a job.

`PriceSource` is the seam. Three variants; workers compose the right one.
The backend calls ``await price_source.estimate(gpu_seconds)`` after each
handle() and populates ``JobMetrics.cost_estimate`` + ``cost_basis`` from
the returned :class:`PriceEstimate`.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from acheron.core.models import CostBasis

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PriceEstimate:
    """Outcome of a price query.

    ``cost is None`` means unknown (provider API unavailable and no cache);
    ``cost == 0.0`` means an actual $0 (stub/local/ZeroPrice).
    """

    cost: float | None
    reason: str | None = None


class PriceSource(Protocol):
    """Provider-agnostic price source."""

    async def estimate(self, gpu_seconds: float) -> PriceEstimate:
        """Return a price estimate for ``gpu_seconds`` of GPU time."""
        ...

    async def refresh(self) -> bool:
        """Force-refresh cached rates; return False on any failure (non-fatal)."""
        ...


@dataclass(frozen=True)
class ZeroPrice:
    """Stubs/local — no cost tracking. Reports $0 with STATIC basis."""

    async def estimate(self, gpu_seconds: float) -> PriceEstimate:  # noqa: ARG002
        """Return a fixed $0 estimate (GPU is local; not metered)."""
        return PriceEstimate(cost=0.0, reason="zero (stub/local)")

    async def refresh(self) -> bool:
        """No-op; returns True so callers can treat this as always-warm."""
        return True


@dataclass(frozen=True)
class StaticPrice:
    """Fixed $/hr from config — operator opted out of API rate lookup."""

    dollars_per_hour: float

    async def estimate(self, gpu_seconds: float) -> PriceEstimate:
        """Compute ``gpu_seconds * $/hr / 3600`` and return with STATIC reason."""
        cost = round(gpu_seconds * self.dollars_per_hour / 3600.0, 6)
        return PriceEstimate(cost=cost, reason="static config")

    async def refresh(self) -> bool:
        """No-op; static rates don't need refreshing."""
        return True


_KNOWN_REASONS: frozenset[str] = frozenset(
    {
        "runpod:measured",
        "runpod:cached",
        "static config",
        "zero (stub/local)",
    }
)


def to_cost_basis(estimate: PriceEstimate) -> CostBasis:
    """Map a :class:`PriceEstimate` to a wire :class:`CostBasis` value.

    RunPodPrice sets ``reason`` to a sentinel string that distinguishes the
    fresh-measurement case from the cached case; the worker-side mapping
    preserves the spec's ``MEASURED`` vs ``CACHED`` distinction. Any new
    ``PriceSource`` must register its ``reason`` in ``_KNOWN_REASONS`` or
    the safety net below raises — failing loud is better than silently
    misclassifying an estimate as ``STATIC``.
    """
    if estimate.cost is None:
        return CostBasis.UNKNOWN
    if estimate.reason == "runpod:measured":
        return CostBasis.MEASURED
    if estimate.reason == "runpod:cached":
        return CostBasis.CACHED
    if estimate.reason in _KNOWN_REASONS:
        return CostBasis.STATIC
    msg = f"Unknown PriceEstimate.reason {estimate.reason!r}; add it to _KNOWN_REASONS"
    raise ValueError(msg)


@dataclass
class RunPodPrice:
    """Pulls $/hr from RunPod GraphQL using the endpoint's configured GPU.

    RunPod is the single source of truth for the GPU type — the worker does
    not configure ``gpu_type``. ``_refresh_rate()`` makes two GraphQL calls:
    (1) read the endpoint's ``gpuIds`` via ``myself { endpoints { id gpuIds } }``,
    (2) resolve ``uninterruptablePrice`` via ``gpuTypes(input: {id: $gpu_id})``.
    Changing the GPU on the RunPod endpoint takes effect on the next
    cache refresh (``cache_ttl_s``).
    """

    api_key: str
    endpoint_id: str
    secure_cloud: bool = False
    cache_ttl_s: float = 3600.0

    _rate: float | None = field(default=None, init=False)
    _rate_fetched_at: float = field(default=0.0, init=False)

    async def refresh(self) -> bool:
        """Force-refresh the rate from RunPod GraphQL.

        ``True`` on success, ``False`` on any failure (caller should treat
        as non-fatal — the cache will be served under CACHED basis).
        """
        async with httpx.AsyncClient() as client:
            return await self._refresh_rate(client)

    async def _refresh_rate(self, client: httpx.AsyncClient) -> bool:
        """Hit the GraphQL endpoint; populate ``_rate``. Return False on any failure."""
        try:
            gpu_id = await self._fetch_gpu_id(client)
            if gpu_id is None:
                return False
            rate = await self._fetch_uninterruptable_price(client, gpu_id)
            if rate is None:
                return False
        except (httpx.HTTPError, OSError, KeyError, ValueError, TypeError) as exc:
            logger.exception(
                "RunPod price refresh failed for endpoint %s: %s",
                self.endpoint_id,
                type(exc).__name__,
            )
            return False
        self._rate = rate
        self._rate_fetched_at = time.monotonic()
        return True

    async def _fetch_gpu_id(self, client: httpx.AsyncClient) -> str | None:
        query = "query { myself { endpoints { id gpuIds } } }"
        resp = await self._post_graphql(client, query)
        endpoints = resp["data"]["myself"].get("endpoints")
        if not endpoints:
            return None
        for ep in endpoints:
            if ep["id"] == self.endpoint_id:
                return str(ep["gpuIds"])
        return None

    async def _fetch_uninterruptable_price(self, client: httpx.AsyncClient, gpu_id: str) -> float | None:
        query = (
            "query($id: String!, $secure: Boolean!) {"
            "  gpuTypes(input: {id: $id}) {"
            "    lowestPrice(input: {gpuCount: 1, secureCloud: $secure}) "
            "{ uninterruptablePrice }"
            "  }"
            "}"
        )
        resp = await self._post_graphql(
            client,
            query,
            variables={"id": gpu_id, "secure": self.secure_cloud},
        )
        gpu_types = resp["data"].get("gpuTypes") or []
        if not gpu_types:
            return None
        return float(gpu_types[0]["lowestPrice"]["uninterruptablePrice"])

    async def _post_graphql(
        self,
        client: httpx.AsyncClient,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resp = await client.post(
            "https://api.runpod.io/graphql",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"query": query, "variables": variables or {}},
            timeout=10.0,
        )
        resp.raise_for_status()
        body: dict[str, Any] = resp.json()
        return body

    async def estimate(self, gpu_seconds: float) -> PriceEstimate:
        """Compute cost; refresh the cached rate if stale or unset."""
        now = time.monotonic()
        stale = self._rate is None or (now - self._rate_fetched_at) > self.cache_ttl_s
        refreshed: bool | None = None
        if stale:
            async with httpx.AsyncClient() as client:
                refreshed = await self._refresh_rate(client)
        if self._rate is None:
            return PriceEstimate(
                cost=None,
                reason=f"runpod pricing unavailable for endpoint {self.endpoint_id}",
            )
        cost = round(gpu_seconds * self._rate / 3600.0, 6)
        if refreshed is False:
            return PriceEstimate(cost=cost, reason="runpod:cached")
        return PriceEstimate(cost=cost, reason="runpod:measured")
