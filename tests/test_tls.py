"""Direct unit tests for ``src/acheron/tls.py`` (TEST-015).

The integration tests in ``tests/integration/test_tls.py`` only exercise
the happy path with real subprocesses and a valid CA bundle. These unit
tests cover the env-var branches, the warning log on insecure fallback,
and the malformed-PEM paths that the integration tests skip.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from acheron.core.errors import AcheronError
from acheron.tls import (
    _require_pair,
    grpc_channel_credentials,
    grpc_server_credentials,
    resolve_ca_path,
    uvicorn_ssl_kwargs,
)


class TestRequirePair:
    def test_returns_none_when_both_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ACHERON_TLS_CERT_FILE", raising=False)
        monkeypatch.delenv("ACHERON_TLS_KEY_FILE", raising=False)
        assert _require_pair() is None

    def test_raises_when_only_cert_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ACHERON_TLS_CERT_FILE", "/c")
        monkeypatch.delenv("ACHERON_TLS_KEY_FILE", raising=False)
        with pytest.raises(AcheronError, match="must be set together"):
            _require_pair()

    def test_raises_when_only_key_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ACHERON_TLS_CERT_FILE", raising=False)
        monkeypatch.setenv("ACHERON_TLS_KEY_FILE", "/k")
        with pytest.raises(AcheronError, match="must be set together"):
            _require_pair()

    def test_returns_pair_when_both_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ACHERON_TLS_CERT_FILE", "/c")
        monkeypatch.setenv("ACHERON_TLS_KEY_FILE", "/k")
        assert _require_pair() == ("/c", "/k")


class TestUvicornSslKwargs:
    def test_returns_empty_dict_with_warning_when_insecure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.delenv("ACHERON_TLS_CERT_FILE", raising=False)
        monkeypatch.delenv("ACHERON_TLS_KEY_FILE", raising=False)
        monkeypatch.delenv("ACHERON_ALLOW_INSECURE", raising=False)
        with caplog.at_level(logging.WARNING, logger="acheron.tls"):
            assert uvicorn_ssl_kwargs() == {}
        assert any("plain HTTP" in r.message for r in caplog.records)

    def test_returns_empty_dict_silently_when_allow_insecure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.delenv("ACHERON_TLS_CERT_FILE", raising=False)
        monkeypatch.delenv("ACHERON_TLS_KEY_FILE", raising=False)
        monkeypatch.setenv("ACHERON_ALLOW_INSECURE", "1")
        with caplog.at_level(logging.WARNING, logger="acheron.tls"):
            assert uvicorn_ssl_kwargs() == {}
        assert not any("plain HTTP" in r.message for r in caplog.records)

    def test_returns_ssl_kwargs_when_pair_set(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ACHERON_TLS_CERT_FILE", "/c")
        monkeypatch.setenv("ACHERON_TLS_KEY_FILE", "/k")
        assert uvicorn_ssl_kwargs() == {"ssl_certfile": "/c", "ssl_keyfile": "/k"}


class TestResolveCaPath:
    def test_returns_none_when_neither_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ACHERON_TLS_CA_FILE", raising=False)
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)
        assert resolve_ca_path() is None

    def test_prefers_acheron_tls_ca_file(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ACHERON_TLS_CA_FILE", "/our-ca.pem")
        monkeypatch.setenv("SSL_CERT_FILE", "/system-ca.pem")
        assert resolve_ca_path() == "/our-ca.pem"

    def test_falls_back_to_ssl_cert_file(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ACHERON_TLS_CA_FILE", raising=False)
        monkeypatch.setenv("SSL_CERT_FILE", "/system-ca.pem")
        assert resolve_ca_path() == "/system-ca.pem"


class TestGrpcCredentials:
    def test_grpc_channel_credentials_returns_none_when_unset(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("ACHERON_TLS_CA_FILE", raising=False)
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)
        assert grpc_channel_credentials() is None

    def test_grpc_channel_credentials_returns_credentials_with_ca(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # PEM content doesn't need to be valid here — grpc only validates on
        # the first channel operation, not at construction time. The
        # contract under test is that grpc_channel_credentials reads the
        # bytes and returns a non-None ChannelCredentials.
        ca = tmp_path / "ca.pem"
        ca.write_bytes(b"---placeholder---")
        monkeypatch.setenv("ACHERON_TLS_CA_FILE", str(ca))
        assert grpc_channel_credentials() is not None

    def test_grpc_server_credentials_returns_credentials_with_pair(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        cert.write_bytes(b"---placeholder---")
        key.write_bytes(b"---placeholder---")
        monkeypatch.setenv("ACHERON_TLS_CERT_FILE", str(cert))
        monkeypatch.setenv("ACHERON_TLS_KEY_FILE", str(key))
        assert grpc_server_credentials() is not None
