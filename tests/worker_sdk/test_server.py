"""Tests for the shared uvicorn server runner used by the worker SDK and orchestrator."""

from __future__ import annotations

import ssl
from typing import Any

import pytest
from fastapi import FastAPI

from acheron.worker_sdk._server import run_worker_server


def _patch_server(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch uvicorn.Server to a no-op mock and return the captured config kwargs."""
    import uvicorn

    captured: dict[str, Any] = {}

    class _FakeServer:
        def __init__(self, config: uvicorn.Config) -> None:
            captured["config"] = config

        def run(self) -> None:
            raise SystemExit(0)

    monkeypatch.setattr(uvicorn, "Server", _FakeServer)
    return captured


class TestRunWorkerServer:
    def test_server_preserves_plain_http_without_tls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ACHERON_TLS_CERT_FILE", raising=False)
        monkeypatch.delenv("ACHERON_TLS_KEY_FILE", raising=False)
        captured = _patch_server(monkeypatch)
        app = FastAPI()

        with pytest.raises(SystemExit):
            run_worker_server(app, host="127.0.0.1", port=9000)

        config = captured["config"]
        assert config.host == "127.0.0.1"
        assert config.port == 9000
        assert config.ssl_certfile is None
        assert config.ssl_keyfile is None
        assert config.ssl_context_factory is None

    def test_server_uses_reloadable_context_when_manager_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured = _patch_server(monkeypatch)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)

        with pytest.raises(SystemExit):
            run_worker_server(FastAPI(), host="127.0.0.1", port=9000, ssl_ctx=context)

        config = captured["config"]
        assert config.ssl_context_factory is not None
        assert config.ssl_context_factory(config, lambda: context) is context
