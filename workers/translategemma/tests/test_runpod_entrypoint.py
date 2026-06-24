"""Tests for runpod_entrypoint.main."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from acheron.worker_sdk.settings import WorkerSettings


def test_entrypoint_module_is_importable() -> None:
    """The entrypoint module imports the cloud-side handler class
    eagerly at module load time.
    """
    from workers.translategemma import runpod_entrypoint

    assert hasattr(runpod_entrypoint, "main")
    from workers.translategemma.handler import TranslateGemmaRunpodHandler

    assert callable(TranslateGemmaRunpodHandler)


def test_main_loads_handler_and_starts_runpod(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_settings = WorkerSettings(
        worker_id="t-1",
        orchestrator_url="http://o:8000",
        listen_port=8001,
        price_source="zero",
    )
    monkeypatch.setattr(
        "workers.translategemma.runpod_entrypoint.load_settings",
        lambda: fake_settings,
    )

    fake_handler = MagicMock()
    fake_handler.startup = AsyncMock()
    fake_handler_class = MagicMock(return_value=fake_handler)
    monkeypatch.setattr(
        "workers.translategemma.runpod_entrypoint.TranslateGemmaRunpodHandler",
        fake_handler_class,
    )

    fake_runpod = MagicMock()
    monkeypatch.setattr("workers.translategemma.runpod_entrypoint.runpod", fake_runpod)

    from workers.translategemma import runpod_entrypoint

    runpod_entrypoint.main()

    fake_handler.startup.assert_awaited_once()
    fake_runpod.serverless.start.assert_called_once()
    call_arg = fake_runpod.serverless.start.call_args[0][0]
    assert "handler" in call_arg
    assert callable(call_arg["handler"])
