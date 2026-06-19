"""Unit tests for the TLS env-var helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from acheron.core.errors import AcheronError
from acheron.shell.tls import (
    grpc_channel_credentials,
    grpc_server_credentials,
    uvicorn_ssl_kwargs,
)


def test_uvicorn_kwargs_empty_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACHERON_TLS_CERT_FILE", raising=False)
    monkeypatch.delenv("ACHERON_TLS_KEY_FILE", raising=False)
    assert uvicorn_ssl_kwargs() == {}


def test_uvicorn_kwargs_raises_when_only_cert_set(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ACHERON_TLS_CERT_FILE", str(tmp_path / "x.crt"))
    monkeypatch.delenv("ACHERON_TLS_KEY_FILE", raising=False)
    with pytest.raises(AcheronError, match="must be set together"):
        uvicorn_ssl_kwargs()


def test_uvicorn_kwargs_raises_when_only_key_set(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ACHERON_TLS_CERT_FILE", raising=False)
    monkeypatch.setenv("ACHERON_TLS_KEY_FILE", str(tmp_path / "x.key"))
    with pytest.raises(AcheronError, match="must be set together"):
        uvicorn_ssl_kwargs()


def test_uvicorn_kwargs_returns_paths_when_both_set(monkeypatch: pytest.MonkeyPatch, dev_certs: Path) -> None:
    monkeypatch.setenv("ACHERON_TLS_CERT_FILE", str(dev_certs / "orchestrator.crt"))
    monkeypatch.setenv("ACHERON_TLS_KEY_FILE", str(dev_certs / "orchestrator.key"))
    assert uvicorn_ssl_kwargs() == {
        "ssl_certfile": str(dev_certs / "orchestrator.crt"),
        "ssl_keyfile": str(dev_certs / "orchestrator.key"),
    }


def test_grpc_server_credentials_none_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ACHERON_TLS_CERT_FILE", raising=False)
    monkeypatch.delenv("ACHERON_TLS_KEY_FILE", raising=False)
    assert grpc_server_credentials() is None


def test_grpc_server_credentials_raises_when_only_cert_set(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ACHERON_TLS_CERT_FILE", str(tmp_path / "x.crt"))
    monkeypatch.delenv("ACHERON_TLS_KEY_FILE", raising=False)
    with pytest.raises(AcheronError, match="must be set together"):
        grpc_server_credentials()


def test_grpc_server_credentials_returns_credentials_when_both_set(
    monkeypatch: pytest.MonkeyPatch, dev_certs: Path
) -> None:
    monkeypatch.setenv("ACHERON_TLS_CERT_FILE", str(dev_certs / "orchestrator.crt"))
    monkeypatch.setenv("ACHERON_TLS_KEY_FILE", str(dev_certs / "orchestrator.key"))
    creds = grpc_server_credentials()
    assert creds is not None


def test_grpc_channel_credentials_none_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ACHERON_TLS_CA_FILE", raising=False)
    assert grpc_channel_credentials() is None


def test_grpc_channel_credentials_returns_credentials_when_set(
    monkeypatch: pytest.MonkeyPatch, dev_certs: Path
) -> None:
    monkeypatch.setenv("ACHERON_TLS_CA_FILE", str(dev_certs / "acheron-ca.crt"))
    creds = grpc_channel_credentials()
    assert creds is not None
