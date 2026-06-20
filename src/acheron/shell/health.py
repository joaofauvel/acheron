"""Background health monitoring for registered workers."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import grpc
import grpc.aio
import httpx
from grpc.health.v1 import health_pb2, health_pb2_grpc

from acheron.shell.tls import grpc_channel

if TYPE_CHECKING:
    from acheron.shell.stores.base import WorkerStore

logger = logging.getLogger(__name__)

type HealthCheckFn = Callable[[str, str], Awaitable[bool]]


async def _check_http_health(endpoint: str) -> bool:
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{endpoint}/health", timeout=5.0)
            return resp.status_code == httpx.codes.OK
    except httpx.HTTPError, OSError:
        return False


async def _check_grpc_health(endpoint: str) -> bool:
    try:
        async with grpc_channel(endpoint) as channel:
            stub = health_pb2_grpc.HealthStub(channel)
            resp = await stub.Check(health_pb2.HealthCheckRequest())
            return resp.status == health_pb2.HealthCheckResponse.SERVING  # type: ignore[no-any-return]
    except grpc.aio.AioRpcError, OSError:
        return False


async def _default_health_check(endpoint: str, transport: str) -> bool:
    match transport:
        case "grpc":
            return await _check_grpc_health(endpoint)
        case "local":
            return True
        case _:
            return await _check_http_health(endpoint)


class HealthMonitor:
    """Periodic background task checking worker health."""

    def __init__(
        self,
        registry: WorkerStore,
        interval: float = 30.0,
        health_check: HealthCheckFn | None = None,
    ) -> None:
        self._registry = registry
        self._interval = interval
        self._health_check = health_check or _default_health_check
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
        """Check health of all registered workers concurrently.

        Workers registered between this snapshot and the per-worker probe
        are not checked until the next interval. With the default 30s
        interval this is fine; no locking needed.
        """
        workers = list(await self._registry.list_all())
        if not workers:
            return
        results = await asyncio.gather(
            *(self._health_check(w.endpoint, w.transport) for w in workers),
            return_exceptions=True,
        )
        for worker, healthy in zip(workers, results, strict=True):
            if isinstance(healthy, BaseException):
                logger.warning("Health check for %s raised: %s", worker.worker_id, healthy)
                healthy = False
            if healthy:
                await self._registry.record_health_success(worker.worker_id)
            else:
                removed = await self._registry.record_health_failure(worker.worker_id)
                if removed:
                    logger.warning("Removed unhealthy worker %s after 3 failures", worker.worker_id)
