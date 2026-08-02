"""Tests for the orchestrator __main__ entry point."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest


def _patch_server(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch uvicorn.Server to a no-op mock and return the captured config kwargs.

    The orchestrator's main() builds ``uvicorn.Config(app, ...)`` and runs it
    via ``uvicorn.Server(config).run()``. We replace Server with a sentinel
    whose ``run()`` raises SystemExit so the test exits the blocking call.
    """
    import uvicorn

    captured: dict[str, Any] = {}

    class _FakeServer:
        def __init__(self, config: uvicorn.Config) -> None:
            captured["config"] = config

        def run(self) -> None:
            raise SystemExit(0)

    monkeypatch.setattr(uvicorn, "Server", _FakeServer)
    return captured


def test_main_invokes_uvicorn_with_tls_kwargs(
    monkeypatch: pytest.MonkeyPatch,
    dev_certs: Path,
    tmp_path: Path,
) -> None:
    """`python -m acheron.shell.api` builds a uvicorn.Config with TLS kwargs set."""
    monkeypatch.setenv("ACHERON_TLS_CERT_FILE", str(dev_certs / "orchestrator.crt"))
    monkeypatch.setenv("ACHERON_TLS_KEY_FILE", str(dev_certs / "orchestrator.key"))
    monkeypatch.setenv("ACHERON_DATA_DIR", str(tmp_path / "data"))
    captured = _patch_server(monkeypatch)
    monkeypatch.setattr("sys.argv", ["acheron.shell.api", "--port", "0"])

    from acheron.shell.api.__main__ import main

    with pytest.raises(SystemExit):
        main()
    config = captured["config"]
    assert config.ssl_certfile == str(dev_certs / "orchestrator.crt")
    assert config.ssl_keyfile == str(dev_certs / "orchestrator.key")
    assert config.host == "0.0.0.0"
    assert config.port == 0


def test_main_invokes_uvicorn_without_tls_when_unset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("ACHERON_TLS_CERT_FILE", raising=False)
    monkeypatch.delenv("ACHERON_TLS_KEY_FILE", raising=False)
    monkeypatch.setenv("ACHERON_DATA_DIR", str(tmp_path / "data"))
    captured = _patch_server(monkeypatch)
    monkeypatch.setattr("sys.argv", ["acheron.shell.api", "--port", "0"])

    from acheron.shell.api.__main__ import main

    with pytest.raises(SystemExit):
        main()
    config = captured["config"]
    assert config.ssl_certfile is None
    assert config.ssl_keyfile is None


@pytest.mark.asyncio
async def test_app_starts_and_stops_certificate_monitor_without_leaking_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from acheron.shell.api import app as app_module

    events: list[str] = []

    class _FakeOrchestrator:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def start(self) -> None:
            events.append("orchestrator.start")

        async def shutdown(self) -> None:
            events.append("orchestrator.shutdown")

        async def close(self) -> None:
            events.append("orchestrator.close")

    class _FakeCertificateManager:
        def __init__(self) -> None:
            self.monitor_task: asyncio.Task[None] | None = None

        async def _monitor(self) -> None:
            await asyncio.Future[None]()

        async def start(self) -> None:
            events.append("certificate.start")
            self.monitor_task = asyncio.create_task(self._monitor())
            await asyncio.sleep(0)

        async def stop(self) -> None:
            events.append("certificate.stop")
            assert self.monitor_task is not None
            self.monitor_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.monitor_task

    manager = _FakeCertificateManager()
    monkeypatch.setattr(app_module, "Orchestrator", _FakeOrchestrator)

    app = app_module.create_app(data_dir=tmp_path / "data", certificate_manager=manager)
    assert app.state.certificate_manager is manager

    async with app.router.lifespan_context(app):
        assert events == ["orchestrator.start", "certificate.start"]

    assert events == [
        "orchestrator.start",
        "certificate.start",
        "certificate.stop",
        "orchestrator.shutdown",
        "orchestrator.close",
    ]
    assert manager.monitor_task is not None
    assert manager.monitor_task.done()
    assert manager.monitor_task.cancelled()


@pytest.mark.asyncio
async def test_certificate_monitor_start_failure_does_not_skip_shutdown(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from acheron.shell.api import app as app_module

    events: list[str] = []

    class _FakeOrchestrator:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def start(self) -> None:
            events.append("orchestrator.start")

        async def shutdown(self) -> None:
            events.append("orchestrator.shutdown")

        async def close(self) -> None:
            events.append("orchestrator.close")

    class _FailingCertificateManager:
        async def start(self) -> None:
            events.append("certificate.start")
            raise RuntimeError("start failed")

        async def stop(self) -> None:
            events.append("certificate.stop")

    monkeypatch.setattr(app_module, "Orchestrator", _FakeOrchestrator)
    app = app_module.create_app(
        data_dir=tmp_path / "data",
        certificate_manager=_FailingCertificateManager(),
    )

    async with app.router.lifespan_context(app):
        assert events == ["orchestrator.start", "certificate.start"]

    assert events == [
        "orchestrator.start",
        "certificate.start",
        "certificate.stop",
        "orchestrator.shutdown",
        "orchestrator.close",
    ]


@pytest.mark.asyncio
async def test_certificate_monitor_stop_failure_does_not_skip_orchestrator_shutdown(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from acheron.shell.api import app as app_module

    events: list[str] = []

    class _FakeOrchestrator:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def start(self) -> None:
            events.append("orchestrator.start")

        async def shutdown(self) -> None:
            events.append("orchestrator.shutdown")

        async def close(self) -> None:
            events.append("orchestrator.close")

    class _FailingCertificateManager:
        async def start(self) -> None:
            events.append("certificate.start")

        async def stop(self) -> None:
            events.append("certificate.stop")
            raise RuntimeError("stop failed")

    monkeypatch.setattr(app_module, "Orchestrator", _FakeOrchestrator)
    app = app_module.create_app(
        data_dir=tmp_path / "data",
        certificate_manager=_FailingCertificateManager(),
    )

    async with app.router.lifespan_context(app):
        assert events == ["orchestrator.start", "certificate.start"]

    assert events == [
        "orchestrator.start",
        "certificate.start",
        "certificate.stop",
        "orchestrator.shutdown",
        "orchestrator.close",
    ]


@pytest.mark.asyncio
async def test_app_without_tls_keeps_existing_startup_behavior(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from acheron.shell.api import app as app_module

    monkeypatch.delenv("ACHERON_TLS_CERT_FILE", raising=False)
    monkeypatch.delenv("ACHERON_TLS_KEY_FILE", raising=False)
    events: list[str] = []

    class _FakeOrchestrator:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def start(self) -> None:
            events.append("orchestrator.start")

        async def shutdown(self) -> None:
            events.append("orchestrator.shutdown")

        async def close(self) -> None:
            events.append("orchestrator.close")

    monkeypatch.setattr(app_module, "Orchestrator", _FakeOrchestrator)
    app = app_module.create_app(data_dir=tmp_path / "data")

    assert app.state.certificate_manager is None
    async with app.router.lifespan_context(app):
        assert events == ["orchestrator.start"]

    assert events == ["orchestrator.start", "orchestrator.shutdown", "orchestrator.close"]
