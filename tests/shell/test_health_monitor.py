"""Tests for the health monitor."""

from __future__ import annotations

import asyncio
import inspect
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import grpc
import grpc.aio
import httpx
import pytest
import pytest_asyncio
from grpc.health.v1 import health, health_pb2, health_pb2_grpc

from acheron.core.interfaces import HealthProvider
from acheron.core.models import JsonValue, WorkerCapabilities, WorkerStatus, WorkerType
from acheron.shell.health import HealthMonitor, HealthProbeResult, _default_health_check, _metadata_str
from acheron.shell.registry import RegisteredWorker
from acheron.shell.stores.memory import InMemoryWorkerStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable


async def _poll_for(
    condition: Callable[[], Awaitable[bool] | bool], *, deadline: float = 3.0, interval: float = 0.01
) -> None:
    """Poll until ``condition()`` returns ``True`` or the deadline expires."""
    end = asyncio.get_running_loop().time() + deadline
    while asyncio.get_running_loop().time() < end:
        result = condition()
        if inspect.iscoroutine(result):
            result = await result
        if result:
            return
        await asyncio.sleep(interval)


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
    @pytest.mark.parametrize(
        ("metadata", "expected"),
        [
            ({}, ""),
            ({"health_provider": None}, ""),
            ({"health_provider": 123}, ""),
            ({"health_provider": "runpod"}, "runpod"),
        ],
    )
    def test_metadata_str_only_returns_strings(self, metadata: dict[str, JsonValue], expected: str) -> None:
        worker = RegisteredWorker(
            worker_id="w1",
            endpoint="http://worker",
            transport="http",
            capabilities=WorkerCapabilities(
                worker_type=WorkerType.TTS,
                supported_languages_in=frozenset(),
                supported_languages_out=frozenset(),
                supported_formats_in=frozenset(),
                supported_formats_out=frozenset(),
                max_payload_bytes=None,
                batch_capable=False,
                model_source=None,
                metadata=metadata,
            ),
        )

        assert _metadata_str(worker, "health_provider") == expected

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
    async def test_default_http_health_reuses_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("w1", "http://worker-1", "http", _tts_caps())
        await reg.register("w2", "http://worker-2", "http", _tts_caps())
        client = AsyncMock()
        client.get.return_value = type("Response", (), {"status_code": httpx.codes.OK})()
        monkeypatch.setattr(httpx, "AsyncClient", lambda: client)
        monitor = HealthMonitor(reg)

        await monitor._check_all()  # noqa: SLF001
        await monitor.stop()

        assert client.get.await_count == 2
        client.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_records_success_for_healthy_worker(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("w1", "http://worker", "http", _tts_caps())
        health_check = AsyncMock(return_value=HealthProbeResult(healthy=True))
        monitor = HealthMonitor(reg, interval=0.01, health_check=health_check)
        await monitor.start()
        await _poll_for(lambda: health_check.called)
        await monitor.stop()
        health_check.assert_called()
        w = await reg.get("w1")
        assert w is not None
        assert w.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_records_failure_for_unhealthy_worker(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("w1", "http://worker", "http", _tts_caps())
        health_check = AsyncMock(return_value=HealthProbeResult(healthy=False, error="down"))
        monitor = HealthMonitor(reg, interval=0.01, health_check=health_check)
        await monitor.start()

        async def _condition() -> bool:
            w = await reg.get("w1")
            return w is None or w.consecutive_failures > 0

        await _poll_for(_condition)
        await monitor.stop()
        w = await reg.get("w1")
        assert w is None or w.consecutive_failures > 0

    @pytest.mark.asyncio
    async def test_removes_worker_after_max_failures(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("w1", "http://worker", "http", _tts_caps())
        health_check = AsyncMock(return_value=HealthProbeResult(healthy=False, error="down"))
        monitor = HealthMonitor(reg, interval=0.01, health_check=health_check)
        await monitor.start()

        async def _condition() -> bool:
            return await reg.get("w1") is None

        await _poll_for(_condition)
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
        assert result.healthy is True

    @pytest.mark.asyncio
    async def test_grpc_unhealthy_worker_returns_false(self) -> None:
        result = await _default_health_check("localhost:1", "grpc")
        assert result.healthy is False

    @pytest.mark.asyncio
    async def test_grpc_does_not_attempt_http(self, grpc_health_server: str) -> None:
        """A gRPC endpoint with no HTTP listener must not be probed via HTTP."""
        result = await _default_health_check(grpc_health_server, "grpc")
        assert result.healthy is True


class TestHealthMonitorTransportAware:
    @pytest.mark.asyncio
    async def test_grpc_worker_not_removed_when_healthy(self, grpc_health_server: str) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("tts-grpc", grpc_health_server, "grpc", _tts_caps())
        monitor = HealthMonitor(reg, interval=0.01)
        await monitor.start()

        async def _condition() -> bool:
            w = await reg.get("tts-grpc")
            return w is not None and w.consecutive_failures == 0

        await _poll_for(_condition)
        await monitor.stop()
        w = await reg.get("tts-grpc")
        assert w is not None
        assert w.consecutive_failures == 0


def _tts_caps_with_provider(provider: str, endpoint_id: str) -> WorkerCapabilities:
    return WorkerCapabilities(
        worker_type=WorkerType.TTS,
        supported_languages_in=frozenset({"es"}),
        supported_languages_out=frozenset({"es"}),
        supported_formats_in=frozenset({"text"}),
        supported_formats_out=frozenset({"wav"}),
        max_payload_bytes=None,
        batch_capable=True,
        model_source=None,
        metadata={"health_provider": provider, "health_endpoint_id": endpoint_id},
    )


class _FakeProvider(HealthProvider):
    """Fake HealthProvider returning a configured status."""

    def __init__(self, status: WorkerStatus) -> None:
        self._status = status
        self.called_with: str | None = None

    async def check_status(self, endpoint_id: str) -> WorkerStatus:
        self.called_with = endpoint_id
        return self._status


class _FlippableProvider(HealthProvider):
    """Fake HealthProvider whose status can be reconfigured mid-test."""

    def __init__(self, status: WorkerStatus) -> None:
        self._status = status
        self.called_with: str | None = None

    def set_status(self, status: WorkerStatus) -> None:
        self._status = status

    async def check_status(self, endpoint_id: str) -> WorkerStatus:
        self.called_with = endpoint_id
        return self._status


class _RaisingProvider(HealthProvider):
    """Fake HealthProvider that always raises."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def check_status(self, endpoint_id: str) -> WorkerStatus:
        raise self._exc


class _BarrierProvider(HealthProvider):
    """Provider that waits until every expected check has started."""

    def __init__(self, expected_calls: int) -> None:
        self._expected_calls = expected_calls
        self._calls = 0
        self._all_called = asyncio.Event()

    async def check_status(self, endpoint_id: str) -> WorkerStatus:
        self._calls += 1
        if self._calls == self._expected_calls:
            self._all_called.set()
        await asyncio.wait_for(self._all_called.wait(), timeout=1.0)
        return WorkerStatus.OFFLINE


class TestHealthMonitorProviderIntegration:
    @pytest.mark.asyncio
    async def test_booting_worker_not_removed(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("w1", "http://down", "http", _tts_caps_with_provider("runpod", "ep-1"))
        fake = _FakeProvider(WorkerStatus.BOOTING)
        providers = {"runpod": fake}
        health_check = AsyncMock(return_value=HealthProbeResult(healthy=False, error="conn refused"))
        monitor = HealthMonitor(reg, interval=0.01, health_check=health_check, providers=providers)
        await monitor.start()

        async def _booting() -> bool:
            w = await reg.get("w1")
            return w is not None and w.status == WorkerStatus.BOOTING

        await _poll_for(_booting)
        await monitor.stop()
        w = await reg.get("w1")
        assert w is not None
        assert w.status == WorkerStatus.BOOTING
        assert w.consecutive_failures == 0
        assert "conn refused" in (w.last_error or "")
        assert fake.called_with == "ep-1"

    @pytest.mark.asyncio
    async def test_booting_worker_is_removed_after_timeout(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("w1", "http://down", "http", _tts_caps_with_provider("runpod", "ep-1"))
        providers = {"runpod": _FakeProvider(WorkerStatus.BOOTING)}
        health_check = AsyncMock(return_value=HealthProbeResult(healthy=False, error="conn refused"))
        monitor = HealthMonitor(
            reg,
            interval=0.01,
            health_check=health_check,
            providers=providers,
        )
        monitor._booting_timeout = 0.0  # noqa: SLF001
        await monitor.start()

        async def _removed() -> bool:
            return await reg.get("w1") is None

        await _poll_for(_removed)
        await monitor.stop()
        assert await reg.get("w1") is None

    @pytest.mark.asyncio
    async def test_removing_booting_worker_clears_timeout_state(self) -> None:
        reg = InMemoryWorkerStore()
        reg.max_failures = 1
        await reg.register("w1", "http://down", "http", _tts_caps_with_provider("runpod", "ep-1"))
        providers = {"runpod": _FakeProvider(WorkerStatus.BOOTING)}
        monitor = HealthMonitor(
            reg,
            health_check=AsyncMock(return_value=HealthProbeResult(healthy=False, error="conn refused")),
            providers=providers,
        )
        monitor._booting_timeout = 0.0  # noqa: SLF001
        worker = await reg.get("w1")
        assert worker is not None

        await monitor._process_result(worker, HealthProbeResult(healthy=False, error="conn refused"))  # noqa: SLF001

        assert await reg.get("w1") is None
        assert "w1" not in monitor._booting_since  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_check_all_clears_state_for_unregistered_workers(self) -> None:
        reg = InMemoryWorkerStore()
        monitor = HealthMonitor(reg)
        monitor._booting_since["w1"] = 1.0  # noqa: SLF001

        await monitor._check_all()  # noqa: SLF001

        assert monitor._booting_since == {}  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_booting_timeout_keeps_worker_offline(self) -> None:
        reg = InMemoryWorkerStore()
        reg.max_failures = 100
        await reg.register("w1", "http://down", "http", _tts_caps_with_provider("runpod", "ep-1"))
        providers = {"runpod": _FakeProvider(WorkerStatus.BOOTING)}
        health_check = AsyncMock(return_value=HealthProbeResult(healthy=False, error="conn refused"))
        monitor = HealthMonitor(reg, interval=0.01, health_check=health_check, providers=providers)
        monitor._booting_timeout = 0.0  # noqa: SLF001
        await monitor.start()

        async def _failed_twice() -> bool:
            worker = await reg.get("w1")
            return worker is not None and worker.consecutive_failures >= 2

        await _poll_for(_failed_twice)
        await monitor.stop()
        worker = await reg.get("w1")
        assert worker is not None
        assert worker.status == WorkerStatus.OFFLINE

    @pytest.mark.asyncio
    async def test_reregistered_worker_gets_fresh_booting_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reg = InMemoryWorkerStore()
        reg.max_failures = 100
        await reg.register("w1", "http://old", "http", _tts_caps_with_provider("runpod", "ep-1"))
        providers = {"runpod": _FakeProvider(WorkerStatus.BOOTING)}
        monitor = HealthMonitor(reg, health_check=AsyncMock(), providers=providers)
        monitor._booting_timeout = 10.0  # noqa: SLF001
        now = 0.0
        monkeypatch.setattr("acheron.shell.health.time.monotonic", lambda: now)

        worker = await reg.get("w1")
        assert worker is not None
        await monitor._process_result(worker, HealthProbeResult(healthy=False, error="starting"))  # noqa: SLF001

        now = 100.0
        await reg.register("w1", "http://new", "http", _tts_caps_with_provider("runpod", "ep-2"))
        worker = await reg.get("w1")
        assert worker is not None
        await monitor._process_result(worker, HealthProbeResult(healthy=False, error="starting"))  # noqa: SLF001

        assert worker.status == WorkerStatus.BOOTING
        assert worker.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_changed_transport_starts_a_fresh_booting_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reg = InMemoryWorkerStore()
        reg.max_failures = 100
        await reg.register("w1", "worker", "http", _tts_caps_with_provider("runpod", "ep-1"))
        monitor = HealthMonitor(reg, providers={"runpod": _FakeProvider(WorkerStatus.BOOTING)})
        monitor._booting_timeout = 10.0  # noqa: SLF001
        now = 0.0
        monkeypatch.setattr("acheron.shell.health.time.monotonic", lambda: now)

        worker = await reg.get("w1")
        assert worker is not None
        await monitor._process_result(worker, HealthProbeResult(healthy=False, error="starting"))  # noqa: SLF001

        now = 100.0
        await reg.register("w1", "worker", "grpc", _tts_caps_with_provider("runpod", "ep-2"))
        worker = await reg.get("w1")
        assert worker is not None
        await monitor._process_result(worker, HealthProbeResult(healthy=False, error="starting"))  # noqa: SLF001

        assert worker.status == WorkerStatus.BOOTING
        assert worker.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_offline_provider_increments_failures(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("w1", "http://down", "http", _tts_caps_with_provider("runpod", "ep-1"))
        fake = _FakeProvider(WorkerStatus.OFFLINE)
        providers = {"runpod": fake}
        health_check = AsyncMock(return_value=HealthProbeResult(healthy=False, error="down"))
        monitor = HealthMonitor(reg, interval=0.01, health_check=health_check, providers=providers)
        await monitor.start()

        async def _removed() -> bool:
            return await reg.get("w1") is None

        await _poll_for(_removed)
        await monitor.stop()
        assert await reg.get("w1") is None

    @pytest.mark.asyncio
    async def test_no_provider_falls_back_to_offline(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("w1", "http://down", "http", _tts_caps())
        health_check = AsyncMock(return_value=HealthProbeResult(healthy=False, error="down"))
        monitor = HealthMonitor(reg, interval=0.01, health_check=health_check)
        await monitor.start()

        async def _offline() -> bool:
            w = await reg.get("w1")
            return w is not None and w.status == WorkerStatus.OFFLINE

        await _poll_for(_offline)
        await monitor.stop()
        w = await reg.get("w1")
        assert w is not None
        assert w.status == WorkerStatus.OFFLINE

    @pytest.mark.asyncio
    async def test_success_resets_to_healthy(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("w1", "http://up", "http", _tts_caps_with_provider("runpod", "ep-1"))
        await reg.set_worker_status("w1", WorkerStatus.BOOTING, "cold")
        health_check = AsyncMock(return_value=HealthProbeResult(healthy=True))
        monitor = HealthMonitor(reg, interval=0.01, health_check=health_check)
        await monitor.start()

        async def _healthy() -> bool:
            w = await reg.get("w1")
            return w is not None and w.status == WorkerStatus.HEALTHY and w.last_error is None

        await _poll_for(_healthy)
        await monitor.stop()

    @pytest.mark.asyncio
    async def test_provider_raises_treated_as_offline(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("w1", "http://down", "http", _tts_caps_with_provider("runpod", "ep-1"))
        providers = {"runpod": _RaisingProvider(httpx.HTTPError("upstream broken"))}
        health_check = AsyncMock(return_value=HealthProbeResult(healthy=False, error="conn refused"))
        monitor = HealthMonitor(reg, interval=0.01, health_check=health_check, providers=providers)
        await monitor.start()

        async def _offline_with_error() -> bool:
            w = await reg.get("w1")
            return (
                w is not None
                and w.status == WorkerStatus.OFFLINE
                and w.consecutive_failures == 1
                and "conn refused" in (w.last_error or "")
                and "upstream broken" in (w.last_error or "")
            )

        await _poll_for(_offline_with_error)
        await monitor.stop()

    @pytest.mark.asyncio
    async def test_provider_runtime_error_propagates(self) -> None:
        """Unexpected exceptions from the provider (e.g. AttributeError from a
        refactor) must propagate so the bug surfaces, not be masked as a
        transient platform error."""
        reg = InMemoryWorkerStore()
        await reg.register("w1", "http://down", "http", _tts_caps_with_provider("runpod", "ep-1"))
        providers = {"runpod": _RaisingProvider(RuntimeError("provider bug"))}
        health_check = AsyncMock(return_value=HealthProbeResult(healthy=False, error="conn refused"))
        monitor = HealthMonitor(reg, interval=0.01, health_check=health_check, providers=providers)
        await monitor.start()
        with pytest.raises(RuntimeError, match="provider bug"):
            await monitor._check_all()  # noqa: SLF001
        await monitor.stop()

    @pytest.mark.asyncio
    async def test_provider_checks_run_concurrently(self) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("w1", "http://down-1", "http", _tts_caps_with_provider("runpod", "ep-1"))
        await reg.register("w2", "http://down-2", "http", _tts_caps_with_provider("runpod", "ep-2"))
        provider = _BarrierProvider(expected_calls=2)
        health_check = AsyncMock(return_value=HealthProbeResult(healthy=False, error="down"))
        monitor = HealthMonitor(reg, health_check=health_check, providers={"runpod": provider})

        await monitor._check_all()  # noqa: SLF001

        assert provider._calls == 2  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_health_result_bookkeeping_runs_concurrently(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reg = InMemoryWorkerStore()
        await reg.register("w1", "http://up-1", "http", _tts_caps())
        await reg.register("w2", "http://up-2", "http", _tts_caps())
        health_check = AsyncMock(return_value=HealthProbeResult(healthy=True))
        monitor = HealthMonitor(reg, health_check=health_check)
        entered: list[str] = []
        all_entered = asyncio.Event()

        async def record_success(worker_id: str) -> None:
            entered.append(worker_id)
            if len(entered) == 2:
                all_entered.set()
            await asyncio.wait_for(all_entered.wait(), timeout=1.0)

        monkeypatch.setattr(reg, "record_health_success", record_success)
        await monitor._check_all()  # noqa: SLF001

        assert entered == ["w1", "w2"]

    @pytest.mark.asyncio
    async def test_booting_transitions_to_offline_when_provider_says_offline(self) -> None:
        """TEST-007: a worker stuck in BOOTING must transition to OFFLINE when
        the platform provider reports the endpoint is offline on a subsequent check.
        """
        reg = InMemoryWorkerStore()
        await reg.register("w1", "http://down", "http", _tts_caps_with_provider("runpod", "ep-1"))
        fake = _FlippableProvider(WorkerStatus.BOOTING)
        providers = {"runpod": fake}
        health_check = AsyncMock(return_value=HealthProbeResult(healthy=False, error="conn refused"))
        monitor = HealthMonitor(reg, interval=0.01, health_check=health_check, providers=providers)
        await monitor.start()

        async def _is_booting() -> bool:
            w = await reg.get("w1")
            return w is not None and w.status == WorkerStatus.BOOTING

        await _poll_for(_is_booting)
        fake.set_status(WorkerStatus.OFFLINE)

        async def _is_offline_with_failure() -> bool:
            w = await reg.get("w1")
            return w is not None and w.status == WorkerStatus.OFFLINE and w.consecutive_failures >= 1

        await _poll_for(_is_offline_with_failure)
        await monitor.stop()
        w = await reg.get("w1")
        assert w is not None
        assert w.status == WorkerStatus.OFFLINE
        assert w.consecutive_failures >= 1

    @pytest.mark.asyncio
    async def test_offline_recovers_to_healthy_on_successful_probe(self) -> None:
        """TEST-007: a worker marked OFFLINE must transition back to HEALTHY
        once its health probe starts succeeding again (recovery path).
        """
        reg = InMemoryWorkerStore()
        await reg.register("w1", "http://up", "http", _tts_caps_with_provider("runpod", "ep-1"))
        await reg.set_worker_status("w1", WorkerStatus.OFFLINE, "previous failure")
        await reg.record_health_failure("w1")
        w_before = await reg.get("w1")
        assert w_before is not None
        assert w_before.status == WorkerStatus.OFFLINE
        assert w_before.consecutive_failures >= 1
        health_check = AsyncMock(return_value=HealthProbeResult(healthy=True))
        monitor = HealthMonitor(reg, interval=0.01, health_check=health_check)
        await monitor.start()

        async def _is_healthy_recovered() -> bool:
            w = await reg.get("w1")
            return (
                w is not None
                and w.status == WorkerStatus.HEALTHY
                and w.consecutive_failures == 0
                and (w.last_error is None or w.last_error == "")
            )

        await _poll_for(_is_healthy_recovered)
        await monitor.stop()
