"""Tests for the health monitor."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import grpc
import grpc.aio
import pytest
import pytest_asyncio
from grpc.health.v1 import health, health_pb2, health_pb2_grpc

from acheron.core.models import WorkerCapabilities, WorkerType
from acheron.shell.health import HealthMonitor, _default_health_check
from acheron.shell.stores.memory import InMemoryWorkerStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _tts_caps() -> WorkerCapabilities:
    return WorkerCapabilities(
        worker_type=WorkerType.TTS,
        supported_languages_in=frozenset({"es"}),
        supported_languages_out=frozenset({"es"}),
        supported_formats_in=frozenset({"text"}),
        supported_formats_out=frozenset({"wav"}),
        max_payload_bytes=None,
        batch_capable=True,
        model_source=None,
    )


class TestHealthMonitor:
    @pytest.mark.asyncio
    async def test_start_and_stop(self) -> None:
        reg = InMemoryWorkerStore()
        monitor = HealthMonitor(reg, interval=0.01)
        await monitor.start()
        assert monitor._task is not None  # noqa: SLF001
        await monitor.stop()
        assert monitor._task.done()  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self) -> None:
        """Calling start() a second time must not replace the running task."""
        reg = InMemoryWorkerStore()
        monitor = HealthMonitor(reg, interval=0.01)
        await monitor.start()
        first_task = monitor._task  # noqa: SLF001
        await monitor.start()
        assert monitor._task is first_task  # noqa: SLF001
        await monitor.stop()

    @pytest.mark.asyncio
    async def test_records_success_for_healthy_worker(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("w1", "http://worker", "http", _tts_caps())
        health_check = AsyncMock(return_value=True)
        monitor = HealthMonitor(reg, interval=0.01, health_check=health_check)
        await monitor.start()
        await asyncio.sleep(0.05)
        await monitor.stop()
        health_check.assert_called()
        w = await reg.get("w1")
        assert w is not None
        assert w.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_records_failure_for_unhealthy_worker(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("w1", "http://worker", "http", _tts_caps())
        health_check = AsyncMock(return_value=False)
        monitor = HealthMonitor(reg, interval=0.01, health_check=health_check)
        await monitor.start()
        await asyncio.sleep(0.05)
        await monitor.stop()
        w = await reg.get("w1")
        assert w is None or w.consecutive_failures > 0

    @pytest.mark.asyncio
    async def test_removes_worker_after_max_failures(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("w1", "http://worker", "http", _tts_caps())
        health_check = AsyncMock(return_value=False)
        monitor = HealthMonitor(reg, interval=0.01, health_check=health_check)
        await monitor.start()
        await asyncio.sleep(0.15)
        await monitor.stop()
        assert await reg.get("w1") is None


@pytest_asyncio.fixture
async def grpc_health_server() -> AsyncIterator[str]:
    """Start an in-process gRPC server with a HealthServicer that reports healthy."""
    server = grpc.aio.server()
    servicer = health.HealthServicer()
    servicer.set("", health_pb2.HealthCheckResponse.SERVING)
    health_pb2_grpc.add_HealthServicer_to_server(servicer, server)
    port = server.add_insecure_port("localhost:0")
    await server.start()
    yield f"localhost:{port}"
    await server.stop(0)


class TestDefaultHealthCheck:
    @pytest.mark.asyncio
    async def test_grpc_worker_uses_grpc_health_check(self, grpc_health_server: str) -> None:
        """gRPC workers are probed via gRPC Health.Check, not HTTP GET /health."""
        result = await _default_health_check(grpc_health_server, "grpc")
        assert result is True

    @pytest.mark.asyncio
    async def test_grpc_unhealthy_worker_returns_false(self) -> None:
        result = await _default_health_check("localhost:1", "grpc")
        assert result is False

    @pytest.mark.asyncio
    async def test_grpc_does_not_attempt_http(self, grpc_health_server: str) -> None:
        """A gRPC endpoint with no HTTP listener must not be probed via HTTP."""
        result = await _default_health_check(grpc_health_server, "grpc")
        assert result is True


class TestHealthMonitorTransportAware:
    @pytest.mark.asyncio
    async def test_grpc_worker_not_removed_when_healthy(self, grpc_health_server: str) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("tts-grpc", grpc_health_server, "grpc", _tts_caps())
        monitor = HealthMonitor(reg, interval=0.01)
        await monitor.start()
        await asyncio.sleep(0.05)
        await monitor.stop()
        w = await reg.get("tts-grpc")
        assert w is not None
        assert w.consecutive_failures == 0
