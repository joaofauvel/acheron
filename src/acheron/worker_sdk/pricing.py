"""Price discovery for Layer 8 workers — fault-tolerant, never blocks a job."""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

import httpx
from pydantic import BaseModel, Field

from acheron.core.models import CostBasis

if TYPE_CHECKING:
    from acheron.core.models import JsonValue

logger = logging.getLogger(__name__)


def _is_valid_rate(rate: float) -> bool:
    return math.isfinite(rate) and rate >= 0.0


@dataclass(frozen=True)
class PriceEstimate:
    """Provider estimate plus the evidence supporting its rate."""

    cost: float | None
    basis: CostBasis
    rate_per_hour: float | None = None
    gpu_type: str | None = None
    secure_cloud: bool | None = None
    queried_at: datetime | None = None
    cache_age_seconds: float | None = None


class PriceSource(Protocol):
    """Provider-agnostic price source."""

    async def estimate(self, gpu_seconds: float) -> PriceEstimate:
        """Return a price estimate for ``gpu_seconds`` of GPU time."""
        ...

    async def refresh(self) -> bool:
        """Force-refresh cached rates; return False on any failure (non-fatal)."""
        ...

    async def close(self) -> None:
        """Release resources owned by the price source."""
        ...


class _GraphQLEndpoint(BaseModel):
    id: str
    gpu_ids: str = Field(alias="gpuIds")


class _GraphQLMyself(BaseModel):
    endpoints: list[_GraphQLEndpoint] | None = None


class _GraphQLLowestPrice(BaseModel):
    uninterruptable_price: float = Field(alias="uninterruptablePrice")


class _GraphQLGpuType(BaseModel):
    lowest_price: _GraphQLLowestPrice = Field(alias="lowestPrice")


class _GraphQLData(BaseModel):
    myself: _GraphQLMyself | None = None
    gpu_types: list[_GraphQLGpuType] | None = Field(default=None, alias="gpuTypes")


class _GraphQLResponse(BaseModel):
    data: _GraphQLData


@dataclass(frozen=True)
class ZeroPrice:
    """Stub/local pricing that is distinct from a configured static rate."""

    async def estimate(self, gpu_seconds: float) -> PriceEstimate:  # noqa: ARG002
        """Return a fixed $0 estimate for an explicitly stubbed worker."""
        return PriceEstimate(cost=0.0, basis=CostBasis.STUB)

    async def refresh(self) -> bool:
        """No-op; returns True so callers can treat this as always-warm."""
        return True

    async def close(self) -> None:
        """Release no resources."""
        return


@dataclass(frozen=True)
class UnknownPrice:
    """Pricing source used when no usable rate configuration exists."""

    async def estimate(self, gpu_seconds: float) -> PriceEstimate:  # noqa: ARG002
        """Return an unknown estimate without implying free execution."""
        return PriceEstimate(cost=None, basis=CostBasis.UNKNOWN)

    async def refresh(self) -> bool:
        """Report that no refresh is possible."""
        return False

    async def close(self) -> None:
        """Release no resources."""
        return


@dataclass(frozen=True)
class StaticPrice:
    """Fixed $/hr from config — operator opted out of API rate lookup."""

    dollars_per_hour: float

    async def estimate(self, gpu_seconds: float) -> PriceEstimate:
        """Compute ``gpu_seconds * $/hr / 3600`` with STATIC provenance."""
        if not _is_valid_rate(self.dollars_per_hour):
            return PriceEstimate(cost=None, basis=CostBasis.UNKNOWN)
        cost = round(gpu_seconds * self.dollars_per_hour / 3600.0, 6)
        return PriceEstimate(
            cost=cost,
            basis=CostBasis.STATIC,
            rate_per_hour=self.dollars_per_hour,
        )

    async def refresh(self) -> bool:
        """No-op; static rates don't need refreshing."""
        return _is_valid_rate(self.dollars_per_hour)

    async def close(self) -> None:
        """Release no resources."""
        return


def to_cost_basis(estimate: PriceEstimate) -> CostBasis:
    """Validate and return the explicit basis carried by an estimate."""
    basis = object.__getattribute__(estimate, "basis")
    if not isinstance(basis, CostBasis):
        msg = f"Invalid PriceEstimate basis {basis!r}"
        raise TypeError(msg)
    if estimate.cost is None:
        return CostBasis.UNKNOWN
    return basis


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
    _gpu_type: str | None = field(default=None, init=False)
    _rate_fetched_at: float = field(default=0.0, init=False)
    _rate_queried_at: datetime | None = field(default=None, init=False)
    _client: httpx.AsyncClient = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._client = httpx.AsyncClient()

    async def close(self) -> None:
        """Close the shared HTTP client."""
        await self._client.aclose()

    async def refresh(self) -> bool:
        """Force-refresh the rate from RunPod GraphQL.

        ``True`` on success, ``False`` on any failure (caller should treat
        as non-fatal — the cache will be served under CACHED basis).
        """
        return await self._refresh_rate(self._client)

    async def _refresh_rate(self, client: httpx.AsyncClient) -> bool:
        """Hit the GraphQL endpoint; populate ``_rate``. Return False on any failure."""
        try:
            gpu_id = await self._fetch_gpu_id(client)
            if gpu_id is None:
                logger.warning("RunPod price refresh found no GPU for endpoint %s", self.endpoint_id)
                return False
            rate = await self._fetch_uninterruptable_price(client, gpu_id)
            if rate is None:
                logger.warning("RunPod price refresh found no rate for endpoint %s", self.endpoint_id)
                return False
            if not _is_valid_rate(rate):
                logger.warning("RunPod price refresh found invalid rate for endpoint %s", self.endpoint_id)
                return False
        except (httpx.HTTPError, OSError, AttributeError, KeyError, ValueError, TypeError) as exc:
            logger.exception(
                "RunPod price refresh failed for endpoint %s: %s",
                self.endpoint_id,
                type(exc).__name__,
            )
            return False
        self._rate = rate
        self._gpu_type = gpu_id
        self._rate_fetched_at = time.monotonic()
        self._rate_queried_at = datetime.now(UTC)
        return True

    async def _fetch_gpu_id(self, client: httpx.AsyncClient) -> str | None:
        query = "query { myself { endpoints { id gpuIds } } }"
        resp = await self._post_graphql(client, query)
        myself = resp.data.myself
        endpoints = myself.endpoints if myself is not None else None
        if not endpoints:
            return None
        for ep in endpoints:
            if ep.id == self.endpoint_id:
                return ep.gpu_ids
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
        gpu_types = resp.data.gpu_types or []
        if not gpu_types:
            return None
        return gpu_types[0].lowest_price.uninterruptable_price

    async def _post_graphql(
        self,
        client: httpx.AsyncClient,
        query: str,
        variables: dict[str, JsonValue] | None = None,
    ) -> _GraphQLResponse:
        resp = await client.post(
            "https://api.runpod.io/graphql",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"query": query, "variables": variables or {}},
            timeout=10.0,
        )
        resp.raise_for_status()
        return _GraphQLResponse.model_validate(resp.json())

    async def estimate(self, gpu_seconds: float) -> PriceEstimate:
        """Return quote metadata; refresh the cached rate if stale or unset."""
        now = time.monotonic()
        stale = self._rate is None or (now - self._rate_fetched_at) > self.cache_ttl_s
        refreshed = await self._refresh_rate(self._client) if stale else None
        if self._rate is None or not _is_valid_rate(self._rate):
            return PriceEstimate(cost=None, basis=CostBasis.UNKNOWN)
        cache_age = 0.0 if refreshed is True else self._cache_age_seconds()
        basis = CostBasis.CACHED if refreshed is False else CostBasis.MEASURED
        return PriceEstimate(
            cost=round(gpu_seconds * self._rate / 3600.0, 6),
            basis=basis,
            rate_per_hour=self._rate,
            gpu_type=self._gpu_type,
            secure_cloud=self.secure_cloud,
            queried_at=self._rate_queried_at,
            cache_age_seconds=cache_age,
        )

    def _cache_age_seconds(self) -> float | None:
        if self._rate_queried_at is None:
            return None
        return max(0.0, (datetime.now(UTC) - self._rate_queried_at).total_seconds())
