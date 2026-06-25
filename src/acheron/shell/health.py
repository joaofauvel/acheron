"""Background health monitoring for registered workers."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import grpc
import grpc.aio
import httpx
from grpc.health.v1 import health_pb2, health_pb2_grpc

from acheron.core.models import WorkerStatus
from acheron.tls import grpc_channel

if TYPE_CHECKING:
    from acheron.shell.health_providers import HealthProviders
    from acheron.shell.registry import RegisteredWorker
    from acheron.shell.stores.base import WorkerStore

logger = logging.getLogger(__name__)

type HealthCheckFn = Callable[[str, str], Awaitable[HealthProbeResult]]


@dataclass(frozen=True)
class HealthProbeResult:
    """Result of a single worker health probe."""

    healthy: bool
    error: str | None = None


def _metadata_str(worker: RegisteredWorker, key: str) -> str:
    """Read a string value from worker capabilities metadata, or "" if absent."""
    value = worker.capabilities.metadata.get(key)
    return value if isinstance(value, str) else ""


async def _check_http_health(endpoint: str) -> HealthProbeResult:
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{endpoint}/health", timeout=5.0)
            if resp.status_code == httpx.codes.OK:
                return HealthProbeResult(healthy=True)
            return HealthProbeResult(healthy=False, error=f"HTTP {resp.status_code}")
    except (httpx.HTTPError, OSError) as exc:
        return HealthProbeResult(healthy=False, error=f"{type(exc).__name__}: {exc}")


async def _check_grpc_health(endpoint: str) -> HealthProbeResult:
    try:
        async with grpc_channel(endpoint) as channel:
            stub = health_pb2_grpc.HealthStub(channel)
            resp = await stub.Check(health_pb2.HealthCheckRequest())
            if resp.status == health_pb2.HealthCheckResponse.SERVING:
                return HealthProbeResult(healthy=True)
            return HealthProbeResult(healthy=False, error=f"gRPC status {resp.status}")
    except (grpc.aio.AioRpcError, OSError) as exc:
        return HealthProbeResult(healthy=False, error=f"{type(exc).__name__}: {exc}")


async def _default_health_check(endpoint: str, transport: str) -> HealthProbeResult:
    match transport:
        case "grpc":
            return await _check_grpc_health(endpoint)
        case "local":
            return HealthProbeResult(healthy=True)
        case _:
            return await _check_http_health(endpoint)


class HealthMonitor:
    """Periodic background task checking worker health."""

    def __init__(
        self,
        registry: WorkerStore,
        interval: float = 30.0,
        health_check: HealthCheckFn | None = None,
        providers: HealthProviders | None = None,
    ) -> None:
        self._registry = registry
        self._interval = interval
        self._health_check = health_check or _default_health_check
        self._providers = providers
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the health check background task. Idempotent."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Stop the health check background task."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _run(self) -> None:
        """Run health checks in a loop."""
        await self._check_all()
        while True:
            await asyncio.sleep(self._interval)
            await self._check_all()

    async def _check_all(self) -> None:
        """Check health of all registered workers concurrently."""
        workers = list(await self._registry.list_all())
        if not workers:
            return
        results = await asyncio.gather(
            *(self._health_check(w.endpoint, w.transport) for w in workers),
            return_exceptions=True,
        )
        for worker, result in zip(workers, results, strict=True):
            if isinstance(result, BaseException):
                logger.warning("Health check for %s raised: %s", worker.worker_id, result)
                outcome = HealthProbeResult(healthy=False, error=f"{type(result).__name__}: {result}")
            else:
                outcome = result
            if outcome.healthy:
                await self._registry.record_health_success(worker.worker_id)
            else:
                await self._handle_failure(worker, outcome.error or "health check failed")

    async def _handle_failure(self, worker: RegisteredWorker, error: str) -> None:
        """On probe failure, consult the platform provider then update status."""
        provider_name = _metadata_str(worker, "health_provider")
        endpoint_id = _metadata_str(worker, "health_endpoint_id")
        provider = self._providers.get(provider_name) if self._providers and provider_name else None
        if provider is not None and endpoint_id:
            try:
                platform_status = await provider.check_status(endpoint_id)
            except (httpx.HTTPError, OSError, ValueError) as exc:
                logger.warning(
                    "Health provider %s raised for worker %s: %s",
                    provider_name,
                    worker.worker_id,
                    exc,
                )
                platform_status = WorkerStatus.OFFLINE
                error = f"{error}; provider {provider_name} error: {exc}"
            if platform_status == WorkerStatus.BOOTING:
                await self._registry.set_worker_status(worker.worker_id, WorkerStatus.BOOTING, error)
                logger.info("Worker %s marked BOOTING via %s", worker.worker_id, provider_name)
                return
        await self._registry.set_worker_status(worker.worker_id, WorkerStatus.OFFLINE, error)
        removed = await self._registry.record_health_failure(worker.worker_id)
        if removed:
            logger.warning("Removed unhealthy worker %s after 3 failures", worker.worker_id)
