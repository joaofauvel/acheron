"""Tests for the orchestrator __main__ entry point."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


def test_main_invokes_uvicorn_with_tls_kwargs(
    monkeypatch: pytest.MonkeyPatch,
    dev_certs: Path,
    tmp_path: Path,
) -> None:
    """`python -m acheron.shell.api` calls uvicorn.run with TLS kwargs set."""
    monkeypatch.setenv("ACHERON_TLS_CERT_FILE", str(dev_certs / "orchestrator.crt"))
    monkeypatch.setenv("ACHERON_TLS_KEY_FILE", str(dev_certs / "orchestrator.key"))
    monkeypatch.setenv("ACHERON_DATA_DIR", str(tmp_path / "data"))
    captured: dict[str, Any] = {}

    def fake_run(app: object, **kwargs: object) -> None:
        captured["app"] = app
        captured.update(kwargs)
        raise SystemExit(0)

    monkeypatch.setattr("uvicorn.run", fake_run)
    monkeypatch.setattr("sys.argv", ["acheron.shell.api", "--port", "0"])

    from acheron.shell.api.__main__ import main

    with pytest.raises(SystemExit):
        main()
    assert captured.get("ssl_certfile") == str(dev_certs / "orchestrator.crt")
    assert captured.get("ssl_keyfile") == str(dev_certs / "orchestrator.key")
    assert captured.get("host") == "0.0.0.0"


def test_main_invokes_uvicorn_without_tls_when_unset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("ACHERON_TLS_CERT_FILE", raising=False)
    monkeypatch.delenv("ACHERON_TLS_KEY_FILE", raising=False)
    monkeypatch.setenv("ACHERON_DATA_DIR", str(tmp_path / "data"))
    captured: dict[str, Any] = {}

    def fake_run(app: object, **kwargs: object) -> None:
        captured.update(kwargs)
        raise SystemExit(0)

    monkeypatch.setattr("uvicorn.run", fake_run)
    monkeypatch.setattr("sys.argv", ["acheron.shell.api", "--port", "0"])

    from acheron.shell.api.__main__ import main

    with pytest.raises(SystemExit):
        main()
    assert captured.get("ssl_certfile") is None
    assert captured.get("ssl_keyfile") is None
