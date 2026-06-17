"""Background health monitoring for registered workers."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from acheron.shell.registry import WorkerRegistry

logger = logging.getLogger(__name__)

type HealthCheckFn = Callable[[str], Awaitable[bool]]


async def _default_health_check(endpoint: str) -> bool:
    """Check worker health via HTTP GET /health."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{endpoint}/health", timeout=5.0)
            return resp.status_code == httpx.codes.OK
    except httpx.HTTPError, OSError:
        return False


class HealthMonitor:
    """Periodic background task checking worker health."""

    def __init__(
        self,
        registry: WorkerRegistry,
        interval: float = 30.0,
        health_check: HealthCheckFn | None = None,
    ) -> None:
        self._registry = registry
        self._interval = interval
        self._health_check = health_check or _default_health_check
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the health check background task."""
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Stop the health check background task."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _run(self) -> None:
        """Run health checks in a loop."""
        while True:
            await asyncio.sleep(self._interval)
            await self._check_all()

    async def _check_all(self) -> None:
        """Check health of all registered workers."""
        for worker in self._registry.list_all():
            healthy = await self._health_check(worker.endpoint)
            if healthy:
                self._registry.record_health_success(worker.worker_id)
            else:
                removed = self._registry.record_health_failure(worker.worker_id)
                if removed:
                    logger.warning("Removed unhealthy worker %s after 3 failures", worker.worker_id)
