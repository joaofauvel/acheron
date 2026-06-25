"""Tests for the orchestrator __main__ entry point."""

from __future__ import annotations

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
