"""Direct unit tests for ``src/acheron/tls.py`` (TEST-015).

The integration tests in ``tests/integration/test_tls.py`` only exercise
the happy path with real subprocesses and a valid CA bundle. These unit
tests cover the env-var branches, the warning log on insecure fallback,
and the malformed-PEM paths that the integration tests skip.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from acheron import tls
from acheron.core.errors import AcheronError
from acheron.tls import (
    _require_pair,
    grpc_channel,
    grpc_channel_credentials,
    grpc_server_credentials,
    resolve_ca_path,
    uvicorn_ssl_kwargs,
)


@dataclass(frozen=True)
class CertificateBundle:
    ca_path: Path
    cert_path: Path
    key_path: Path
    expires_at: datetime


CertificateFactory = Callable[[datetime], CertificateBundle]


@pytest.fixture
def certificate_bundle(tmp_path: Path) -> CertificateFactory:
    def build(expires_at: datetime) -> CertificateBundle:
        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Acheron Test CA")])
        ca_cert = (
            x509.CertificateBuilder()
            .subject_name(ca_name)
            .issuer_name(ca_name)
            .public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(expires_at - timedelta(days=365))
            .not_valid_after(expires_at + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(ca_key, hashes.SHA256())
        )

        server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        server_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "orchestrator")])
        server_cert = (
            x509.CertificateBuilder()
            .subject_name(server_name)
            .issuer_name(ca_name)
            .public_key(server_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(expires_at - timedelta(days=365))
            .not_valid_after(expires_at)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("orchestrator")]), critical=False)
            .sign(ca_key, hashes.SHA256())
        )

        ca_path = tmp_path / "ca.pem"
        cert_path = tmp_path / "orchestrator.crt"
        key_path = tmp_path / "orchestrator.key"
        ca_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
        cert_path.write_bytes(server_cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            server_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )
        return CertificateBundle(ca_path, cert_path, key_path, expires_at)

    return build


@pytest.fixture
def mutable_utc_clock() -> list[datetime]:
    return [datetime(2026, 8, 2, tzinfo=UTC)]


def _manager(bundle: CertificateBundle) -> tls.CertificateManager:
    return tls.CertificateManager(
        cert_path=bundle.cert_path,
        key_path=bundle.key_path,
        name="orchestrator.crt",
    )


class TestCertificateManager:
    def test_certificate_status_reports_subject_and_remaining_time(
        self,
        certificate_bundle: CertificateFactory,
        mutable_utc_clock: list[datetime],
    ) -> None:
        now = mutable_utc_clock[0]
        bundle = certificate_bundle(now + timedelta(days=31))
        status = _manager(bundle).status(now=now)

        assert status is not None
        assert status.name == "orchestrator.crt"
        assert status.subject == "CN=orchestrator"
        assert status.expires_at == bundle.expires_at
        assert status.remaining == timedelta(days=31)
        assert status.severity == "ok"

    def test_certificate_status_formats_sub_day_remaining_time(
        self,
        certificate_bundle: CertificateFactory,
        mutable_utc_clock: list[datetime],
    ) -> None:
        now = mutable_utc_clock[0]
        bundle = certificate_bundle(now + timedelta(hours=1, minutes=2, seconds=3))

        status = _manager(bundle).status(now=now)

        assert status is not None
        assert status.remaining_display == "0d 1h 2m"

    def test_certificate_status_normalizes_utc_timestamps(
        self,
        certificate_bundle: CertificateFactory,
    ) -> None:
        offset = timezone(timedelta(hours=2))
        local_now = datetime(2026, 8, 2, 2, tzinfo=offset)
        bundle = certificate_bundle(local_now + timedelta(days=31))

        status = _manager(bundle).status(now=local_now)

        assert status is not None
        assert status.expires_at == bundle.expires_at.astimezone(UTC)
        assert status.remaining == timedelta(days=31)
        assert status.expires_at.tzinfo is UTC

    def test_certificate_monitor_logs_startup_info(
        self,
        certificate_bundle: CertificateFactory,
        mutable_utc_clock: list[datetime],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        now = mutable_utc_clock[0]
        bundle = certificate_bundle(now + timedelta(days=31))
        manager = _manager(bundle)

        with caplog.at_level(logging.INFO, logger="acheron.tls"):
            manager.check_and_log(now=now)

        startup_records = [
            record
            for record in caplog.records
            if record.levelno == logging.INFO
            and "orchestrator.crt" in record.message
            and "CN=orchestrator" in record.message
        ]
        assert len(startup_records) == 1
        startup_message = startup_records[0].message
        assert f"expires={bundle.expires_at.isoformat()}" in startup_message
        assert "remaining=31d 0h 0m" in startup_message
        assert str(bundle.cert_path) not in startup_message
        assert str(bundle.key_path) not in startup_message
        assert "BEGIN CERTIFICATE" not in startup_message
        assert "PRIVATE KEY" not in startup_message

    def test_certificate_monitor_emits_30_day_warning(
        self,
        certificate_bundle: CertificateFactory,
        mutable_utc_clock: list[datetime],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        now = mutable_utc_clock[0]
        bundle = certificate_bundle(now + timedelta(days=30))

        manager = _manager(bundle)
        with caplog.at_level(logging.WARNING, logger="acheron.tls"):
            manager.check_and_log(now=now)
            manager.check_and_log(now=now)

        threshold_records = [
            record for record in caplog.records if record.message == "Certificate orchestrator.crt expires in 30 days"
        ]
        assert len(threshold_records) == 1
        assert threshold_records[0].levelno == logging.WARNING

    def test_certificate_monitor_emits_7_day_warning_once(
        self,
        certificate_bundle: CertificateFactory,
        mutable_utc_clock: list[datetime],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        now = mutable_utc_clock[0]
        bundle = certificate_bundle(now + timedelta(days=7))
        manager = _manager(bundle)

        with caplog.at_level(logging.WARNING, logger="acheron.tls"):
            manager.check_and_log(now=now)
            manager.check_and_log(now=now)

        warnings = [
            record for record in caplog.records if record.message == "Certificate orchestrator.crt expires in 7 days"
        ]
        assert len(warnings) == 1
        assert warnings[0].levelno == logging.WARNING

    def test_certificate_monitor_emits_1_day_error(
        self,
        certificate_bundle: CertificateFactory,
        mutable_utc_clock: list[datetime],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        now = mutable_utc_clock[0]
        bundle = certificate_bundle(now + timedelta(days=1))

        manager = _manager(bundle)
        with caplog.at_level(logging.ERROR, logger="acheron.tls"):
            manager.check_and_log(now=now)
            manager.check_and_log(now=now)

        errors = [
            record for record in caplog.records if record.message == "Certificate orchestrator.crt expires in 1 day"
        ]
        assert len(errors) == 1
        assert errors[0].levelno == logging.ERROR

    def test_certificate_monitor_emits_expiry_critical(
        self,
        certificate_bundle: CertificateFactory,
        mutable_utc_clock: list[datetime],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        now = mutable_utc_clock[0]
        bundle = certificate_bundle(now)
        manager = _manager(bundle)

        with caplog.at_level(logging.CRITICAL, logger="acheron.tls"):
            manager.check_and_log(now=now)
            manager.check_and_log(now=now)

        critical = [record for record in caplog.records if record.message == "Certificate orchestrator.crt has expired"]
        assert len(critical) == 1
        assert critical[0].levelno == logging.CRITICAL

    def test_certificate_manager_reload_rejects_invalid_pair_without_mutation(
        self,
        certificate_bundle: CertificateFactory,
        mutable_utc_clock: list[datetime],
    ) -> None:
        now = mutable_utc_clock[0]
        bundle = certificate_bundle(now + timedelta(days=31))
        manager = _manager(bundle)
        context_before = manager.ssl_context
        bundle.cert_path.write_text("not a certificate", encoding="utf-8")

        with pytest.raises(AcheronError, match=r"certificate|TLS|PEM"):
            manager.reload()

        assert manager.ssl_context is context_before

    def test_certificate_manager_reload_keeps_old_context_when_status_fails_late(
        self,
        certificate_bundle: CertificateFactory,
        mutable_utc_clock: list[datetime],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class TrackingContext:
            active_certificate = "old"

            def load_cert_chain(self, *, certfile: str, keyfile: str) -> None:
                del certfile, keyfile
                self.active_certificate = "replacement"

        now = mutable_utc_clock[0]
        bundle = certificate_bundle(now + timedelta(days=31))
        manager = _manager(bundle)
        context = TrackingContext()
        manager.ssl_context = context

        def fail_status(*args: object, **kwargs: object) -> tls.CertificateStatus:
            del args, kwargs
            raise tls.CertificateError("status failed")

        monkeypatch.setattr(manager, "_status_for_certificate", fail_status)

        with pytest.raises(tls.CertificateError, match="reload"):
            manager.reload()

        assert context.active_certificate == "old"

    @pytest.mark.asyncio
    async def test_certificate_monitor_start_and_stop_are_deterministic(
        self,
        certificate_bundle: CertificateFactory,
        mutable_utc_clock: list[datetime],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        now = mutable_utc_clock[0]
        bundle = certificate_bundle(now + timedelta(days=31))
        manager = _manager(bundle)
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def controlled_monitor() -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        monkeypatch.setattr(manager, "_monitor", controlled_monitor)

        await manager.start()
        await asyncio.wait_for(started.wait(), timeout=1)

        await manager.stop()

        assert cancelled.is_set()

    def test_manager_is_disabled_when_tls_pair_is_unset(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("ACHERON_TLS_CERT_FILE", raising=False)
        monkeypatch.delenv("ACHERON_TLS_KEY_FILE", raising=False)

        assert tls.CertificateManager.from_env() is None


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
    def test_grpc_channel_sets_receive_bound(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ACHERON_TLS_CA_FILE", raising=False)
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)
        monkeypatch.setenv("ACHERON_ALLOW_INSECURE", "1")
        with patch("acheron.tls.grpc.aio.insecure_channel", return_value=object()) as factory:
            grpc_channel("worker:9000")
        assert factory.call_args.kwargs["options"] == (("grpc.max_receive_message_length", 64 * 1024 * 1024),)

    def test_grpc_channel_insecure_opt_in_takes_precedence_over_ca(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ACHERON_ALLOW_INSECURE", "1")
        monkeypatch.setenv("SSL_CERT_FILE", "/ambient-system-ca.pem")
        with (
            patch("acheron.tls.grpc.aio.insecure_channel", return_value=object()) as insecure,
            patch("acheron.tls.grpc.aio.secure_channel") as secure,
        ):
            grpc_channel("worker:9000")
        insecure.assert_called_once()
        secure.assert_not_called()
        assert insecure.call_args.kwargs["options"] == (("grpc.max_receive_message_length", 64 * 1024 * 1024),)

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
