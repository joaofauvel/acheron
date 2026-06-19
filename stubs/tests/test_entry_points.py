"""Tests for HTTP worker stub __main__ entry points."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


def test_worker_stub_main_invokes_uvicorn_with_tls(monkeypatch: pytest.MonkeyPatch, dev_certs: Path) -> None:
    monkeypatch.setenv("WORKER_TYPE", "TTS")
    monkeypatch.setenv("WORKER_ENDPOINT", "https://tts-stub:8001")
    monkeypatch.setenv("ORCHESTRATOR_URL", "https://orchestrator:8000")
    monkeypatch.setenv("WORKER_PORT", "0")
    monkeypatch.setenv("ACHERON_TLS_CERT_FILE", str(dev_certs / "tts-stub.crt"))
    monkeypatch.setenv("ACHERON_TLS_KEY_FILE", str(dev_certs / "tts-stub.key"))
    captured: dict[str, Any] = {}

    def fake_run(app: object, **kwargs: object) -> None:
        captured.update(kwargs)
        raise SystemExit(0)

    monkeypatch.setattr("uvicorn.run", fake_run)
    from stubs.worker_stub import main

    with pytest.raises(SystemExit):
        main()
    assert captured["ssl_certfile"] == str(dev_certs / "tts-stub.crt")
    assert captured["ssl_keyfile"] == str(dev_certs / "tts-stub.key")


def test_worker_stub_main_invokes_uvicorn_without_tls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKER_TYPE", "TTS")
    monkeypatch.setenv("WORKER_ENDPOINT", "http://tts-stub:8001")
    monkeypatch.setenv("ORCHESTRATOR_URL", "http://orchestrator:8000")
    monkeypatch.setenv("WORKER_PORT", "0")
    monkeypatch.delenv("ACHERON_TLS_CERT_FILE", raising=False)
    monkeypatch.delenv("ACHERON_TLS_KEY_FILE", raising=False)
    captured: dict[str, Any] = {}

    def fake_run(app: object, **kwargs: object) -> None:
        captured.update(kwargs)
        raise SystemExit(0)

    monkeypatch.setattr("uvicorn.run", fake_run)
    from stubs.worker_stub import main

    with pytest.raises(SystemExit):
        main()
    assert "ssl_certfile" not in captured


def test_translation_stub_main_invokes_uvicorn_with_tls(monkeypatch: pytest.MonkeyPatch, dev_certs: Path) -> None:
    monkeypatch.setenv("WORKER_TYPE", "TRANSLATION")
    monkeypatch.setenv("WORKER_ENDPOINT", "https://translation-stub:8003")
    monkeypatch.setenv("ORCHESTRATOR_URL", "https://orchestrator:8000")
    monkeypatch.setenv("ACHERON_TLS_CERT_FILE", str(dev_certs / "translation-stub.crt"))
    monkeypatch.setenv("ACHERON_TLS_KEY_FILE", str(dev_certs / "translation-stub.key"))
    captured: dict[str, Any] = {}

    def fake_run(app: object, **kwargs: object) -> None:
        captured.update(kwargs)
        raise SystemExit(0)

    monkeypatch.setattr("uvicorn.run", fake_run)
    from stubs.translation_stub import main

    with pytest.raises(SystemExit):
        main()
    assert captured["ssl_certfile"] == str(dev_certs / "translation-stub.crt")
