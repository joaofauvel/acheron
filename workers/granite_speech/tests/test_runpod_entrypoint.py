"""Tests for runpod_entrypoint.main."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from acheron.worker_sdk.settings import WorkerSettings


def test_main_loads_handler_and_starts_runpod(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_settings = WorkerSettings(
        worker_id="g-1",
        orchestrator_url="http://o:8000",
        listen_port=8001,
        price_source="zero",
    )
    monkeypatch.setattr(
        "acheron.worker_sdk.config_loader.load_settings",
        lambda: fake_settings,
    )

    fake_handler = MagicMock()
    fake_handler.startup = AsyncMock()
    fake_handler_class = MagicMock(return_value=fake_handler)
    monkeypatch.setattr(
        "workers.granite_speech.runpod_entrypoint.GraniteSpeechRunpodHandler",
        fake_handler_class,
    )

    fake_runpod = MagicMock()
    monkeypatch.setattr("workers.granite_speech.runpod_entrypoint.runpod", fake_runpod)

    from workers.granite_speech import runpod_entrypoint

    runpod_entrypoint.main()

    fake_handler.startup.assert_awaited_once()
    fake_runpod.serverless.start.assert_called_once()
    call_arg = fake_runpod.serverless.start.call_args[0][0]
    assert "handler" in call_arg
    assert callable(call_arg["handler"])
