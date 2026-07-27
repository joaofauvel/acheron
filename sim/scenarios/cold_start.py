"""Cold start scenario — health monitor flips worker BOOTING -> HEALTHY.

STORY_REF: MAINT-009
OPS-006 — `BOOTING` workers show no countdown.

user_journey: "Operator submits a job to a worker in `BOOTING`, sees the worker
table column show `BOOTING (3m 22s elapsed)` with a progress bar that reaches
100% at `_BOOTING_TIMEOUT_SECONDS`."
"""

from __future__ import annotations

import asyncio
import json
import time

import httpx

from acheron.core.interfaces import HealthProvider
from acheron.core.models import WorkerCapabilities, WorkerStatus, WorkerType
from acheron.shell.health import HealthMonitor, HealthProbeResult
from acheron.shell.stores.memory import InMemoryWorkerStore
from sim import MOCK_URL, reset_mock


class _MockRunPodHealthProvider(HealthProvider):
    """Maps mock /endpoints/{id} status to WorkerStatus."""

    def __init__(self, mock_url: str) -> None:
        self._mock_url = mock_url.rstrip("/")

    async def check_status(self, endpoint_id: str) -> WorkerStatus:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self._mock_url}/endpoints/{endpoint_id}",
                    timeout=2.0,
                )
        except httpx.HTTPError, OSError:
            return WorkerStatus.OFFLINE
        if resp.status_code == httpx.codes.OK:
            return WorkerStatus.BOOTING
        return WorkerStatus.OFFLINE


def _make_cold_health_check(healthy_after_s: float):
    start = time.monotonic()

    async def _check(endpoint: str, transport: str) -> HealthProbeResult:
        if time.monotonic() - start < healthy_after_s:
            return HealthProbeResult(healthy=False, error="cold start")
        return HealthProbeResult(healthy=True)

    return _check


async def _run() -> int:
    async with httpx.AsyncClient() as admin:
        await reset_mock(admin)
        r = await admin.post(f"{MOCK_URL}/_admin/control", json={"toggle": "cold_start_ms", "value": 2000})
        if not r.json().get("ok"):
            msg = f"failed to set cold_start_ms: {r.json()}"
            raise AssertionError(msg)

    registry = InMemoryWorkerStore()
    caps = WorkerCapabilities(
        worker_type=WorkerType.TTS,
        supported_languages_in=frozenset({"en"}),
        supported_languages_out=frozenset({"en"}),
        supported_formats_in=frozenset({"text"}),
        supported_formats_out=frozenset({"wav"}),
        max_payload_bytes=None,
        batch_capable=True,
        model_source=None,
        metadata={"health_provider": "runpod", "health_endpoint_id": "qwen-edge"},
    )
    await registry.register("cold-worker", "http://127.0.0.1:1", "http", caps)

    monitor = HealthMonitor(
        registry=registry,
        interval=0.2,
        health_check=_make_cold_health_check(healthy_after_s=1.0),
        providers={"runpod": _MockRunPodHealthProvider(MOCK_URL)},
    )
    await monitor.start()
    try:
        await asyncio.sleep(0.5)
        workers = await registry.list_all()
        status_during_cold_start = workers[0].status
        if status_during_cold_start != WorkerStatus.BOOTING:
            msg = f"after cold start: expected BOOTING, got {status_during_cold_start}"
            raise AssertionError(msg)

        await asyncio.sleep(1.5)
        workers = await registry.list_all()
        status_after_cold_start = workers[0].status
        if status_after_cold_start != WorkerStatus.HEALTHY:
            msg = f"after warm: expected HEALTHY, got {status_after_cold_start}"
            raise AssertionError(msg)

        oracle = {
            "during_cold_start": status_during_cold_start.name,
            "after_cold_start": status_after_cold_start.name,
        }
        assert oracle == {"during_cold_start": "BOOTING", "after_cold_start": "HEALTHY"}
        print(json.dumps({"scenario": "cold_start", "oracle": oracle}, sort_keys=True))
    finally:
        await monitor.stop()

    print("STORY_REF: MAINT-009 ... OK")
    return 0


def main() -> int:
    return asyncio.run(_run())
