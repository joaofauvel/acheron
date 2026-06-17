"""Tests for the health monitor."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from acheron.core.models import WorkerCapabilities, WorkerType
from acheron.shell.health import HealthMonitor
from acheron.shell.registry import WorkerRegistry


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
        reg = WorkerRegistry()
        monitor = HealthMonitor(reg, interval=0.01)
        await monitor.start()
        assert monitor._task is not None  # noqa: SLF001
        await monitor.stop()
        assert monitor._task.done()  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_records_success_for_healthy_worker(self) -> None:
        reg = WorkerRegistry()
        reg.register("w1", "http://worker", "http", _tts_caps())
        health_check = AsyncMock(return_value=True)
        monitor = HealthMonitor(reg, interval=0.01, health_check=health_check)
        await monitor.start()
        await asyncio.sleep(0.05)
        await monitor.stop()
        health_check.assert_called()
        w = reg.get("w1")
        assert w is not None
        assert w.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_records_failure_for_unhealthy_worker(self) -> None:
        reg = WorkerRegistry()
        reg.register("w1", "http://worker", "http", _tts_caps())
        health_check = AsyncMock(return_value=False)
        monitor = HealthMonitor(reg, interval=0.01, health_check=health_check)
        await monitor.start()
        await asyncio.sleep(0.05)
        await monitor.stop()
        w = reg.get("w1")
        assert w is None or w.consecutive_failures > 0

    @pytest.mark.asyncio
    async def test_removes_worker_after_max_failures(self) -> None:
        reg = WorkerRegistry()
        reg.register("w1", "http://worker", "http", _tts_caps())
        health_check = AsyncMock(return_value=False)
        monitor = HealthMonitor(reg, interval=0.01, health_check=health_check)
        await monitor.start()
        await asyncio.sleep(0.15)
        await monitor.stop()
        assert reg.get("w1") is None
