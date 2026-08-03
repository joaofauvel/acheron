"""TLS helpers — env-var to SSL credentials conversion for HTTP and gRPC."""

from __future__ import annotations

import asyncio
import logging
import os
import ssl
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import grpc
import grpc.aio
from cryptography import x509

from acheron.core.errors import AcheronError

_LOG = logging.getLogger(__name__)
_GRPC_MAX_RECEIVE_MESSAGE_BYTES = 64 * 1024 * 1024

CertificateSeverity = Literal["ok", "warning", "error", "critical"]


@dataclass(frozen=True, slots=True)
class CertificateStatus:
    """Public status for an active TLS certificate."""

    name: str
    subject: str
    expires_at: datetime
    remaining: timedelta
    severity: CertificateSeverity

    @property
    def remaining_display(self) -> str:
        """Format remaining time as whole days, hours, and minutes."""
        seconds = max(0, int(self.remaining.total_seconds()))
        days, remainder = divmod(seconds, 24 * 60 * 60)
        hours, remainder = divmod(remainder, 60 * 60)
        minutes = remainder // 60
        return f"{days}d {hours}h {minutes}m"


class CertificateError(AcheronError):
    """A certificate could not be parsed, loaded, or reloaded."""


class CertificateManager:
    """Monitor and reload one server certificate and its persistent context."""

    _MONITOR_INTERVAL = timedelta(days=1)

    def __init__(self, *, cert_path: Path, key_path: Path, name: str) -> None:
        self.cert_path = cert_path
        self.key_path = key_path
        self.name = name
        self.ssl_context = self._load_context()
        self._emitted_thresholds: set[int] = set()
        self._startup_logged = False
        self._monitor_task: asyncio.Task[None] | None = None

    @classmethod
    def from_env(cls, *, name: str = "orchestrator.crt") -> CertificateManager | None:
        """Create a manager from the configured certificate/key pair."""
        pair = _require_pair()
        if pair is None:
            return None
        cert_path, key_path = pair
        return cls(cert_path=Path(cert_path), key_path=Path(key_path), name=name)

    def _load_context(self) -> ssl.SSLContext:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        try:
            context.load_cert_chain(certfile=str(self.cert_path), keyfile=str(self.key_path))
        except (OSError, ssl.SSLError, ValueError) as exc:
            message = f"Unable to load TLS certificate pair for {self.name}"
            raise CertificateError(
                message,
                remediation="Check the certificate and key files before retrying",
            ) from exc
        return context

    def _certificate(self) -> x509.Certificate:
        try:
            return x509.load_pem_x509_certificate(self.cert_path.read_bytes())
        except (OSError, ValueError) as exc:
            message = f"Unable to parse TLS certificate for {self.name}"
            raise CertificateError(message, remediation="Check the certificate file before retrying") from exc

    @staticmethod
    def _utc_now(now: datetime | None) -> datetime:
        current = datetime.now(UTC) if now is None else now
        return current.replace(tzinfo=UTC) if current.tzinfo is None else current.astimezone(UTC)

    @staticmethod
    def _severity(remaining: timedelta) -> CertificateSeverity:
        if remaining <= timedelta(0):
            return "critical"
        if remaining <= timedelta(days=1):
            return "error"
        if remaining <= timedelta(days=30):
            return "warning"
        return "ok"

    def _status_for_certificate(
        self,
        certificate: x509.Certificate,
        now: datetime | None = None,
    ) -> CertificateStatus:
        expires_at = certificate.not_valid_after_utc.astimezone(UTC)
        remaining = expires_at - self._utc_now(now)
        return CertificateStatus(
            name=self.name,
            subject=certificate.subject.rfc4514_string(),
            expires_at=expires_at,
            remaining=remaining,
            severity=self._severity(remaining),
        )

    def status(self, now: datetime | None = None) -> CertificateStatus | None:
        """Return current certificate metadata and expiry severity."""
        return self._status_for_certificate(self._certificate(), now=now)

    def _threshold(self, status: CertificateStatus) -> tuple[int, int, str] | None:
        remaining = status.remaining
        if remaining <= timedelta(0):
            return (0, logging.CRITICAL, f"Certificate {self.name} has expired")
        if remaining <= timedelta(days=1):
            return (1, logging.ERROR, f"Certificate {self.name} expires in 1 day")
        if remaining <= timedelta(days=7):
            return (7, logging.WARNING, f"Certificate {self.name} expires in 7 days")
        if remaining <= timedelta(days=30):
            return (30, logging.WARNING, f"Certificate {self.name} expires in 30 days")
        return None

    def check_and_log(self, now: datetime | None = None) -> CertificateStatus | None:
        """Log startup metadata and the next unreported expiry threshold."""
        status = self.status(now=now)
        if status is None:
            return None
        if not self._startup_logged:
            _LOG.info(
                "Certificate %s subject=%s expires=%s remaining=%s",
                status.name,
                status.subject,
                status.expires_at.isoformat(),
                status.remaining_display,
            )
            self._startup_logged = True
        threshold = self._threshold(status)
        if threshold is not None:
            marker, level, message = threshold
            if marker not in self._emitted_thresholds:
                _LOG.log(level, message)
                self._emitted_thresholds.add(marker)
        return status

    def reload(self) -> CertificateStatus:
        """Validate and activate the current certificate/key pair."""
        try:
            certificate = self._certificate()
            status = self._status_for_certificate(certificate)
            self._load_context()
            self.ssl_context.load_cert_chain(certfile=str(self.cert_path), keyfile=str(self.key_path))
        except (OSError, ssl.SSLError, ValueError, CertificateError) as exc:
            message = f"Unable to reload TLS certificate pair for {self.name}"
            raise CertificateError(
                message,
                remediation="Check the replacement certificate and key before retrying",
            ) from exc
        self._emitted_thresholds.clear()
        self._startup_logged = False
        return status

    async def start(self) -> None:
        """Start the daily certificate monitor."""
        if self._monitor_task is not None:
            return
        self.check_and_log()
        self._monitor_task = asyncio.create_task(self._monitor())

    async def stop(self) -> None:
        """Stop the daily certificate monitor."""
        task = self._monitor_task
        if task is None:
            return
        self._monitor_task = None
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _monitor(self) -> None:
        while True:
            await asyncio.sleep(self._MONITOR_INTERVAL.total_seconds())
            try:
                self.check_and_log()
            except CertificateError:
                _LOG.exception("Certificate monitoring failed for %s", self.name)


def _allow_insecure(url: str | None = None) -> bool:
    """Allow plaintext only for explicitly listed local edge hosts."""
    hosts = os.environ.get("ACHERON_INSECURE_LOCAL_EDGE_HOSTS")
    if url is not None and hosts is not None:
        hostname = urlsplit(url).hostname
        allowed = {item.strip().casefold() for item in hosts.split(",") if item.strip()}
        return hostname is not None and hostname.casefold() in allowed
    return os.environ.get("ACHERON_ALLOW_INSECURE") == "1"


def _require_pair() -> tuple[str, str] | None:
    """Return (cert, key) if both are set, None if both are unset.

    Raises AcheronError if only one is set.
    """
    cert = os.environ.get("ACHERON_TLS_CERT_FILE")
    key = os.environ.get("ACHERON_TLS_KEY_FILE")
    if cert is None and key is None:
        return None
    if cert is None or key is None:
        msg = "ACHERON_TLS_CERT_FILE and ACHERON_TLS_KEY_FILE must be set together"
        raise AcheronError(msg)
    return cert, key


def uvicorn_ssl_kwargs() -> dict[str, str]:
    """Return uvicorn kwargs to enable TLS, or `{}` if TLS is not configured.

    Both `ACHERON_TLS_CERT_FILE` and `ACHERON_TLS_KEY_FILE` must be set together.
    If neither is set, returns `{}` (plaintext HTTP) but logs a WARNING unless
    `ACHERON_ALLOW_INSECURE=1` is set explicitly.
    """
    pair = _require_pair()
    if pair is None:
        if not _allow_insecure():
            _LOG.warning(
                "ACHERON_TLS_CERT_FILE and ACHERON_TLS_KEY_FILE are unset — serving plain HTTP. "
                "Set both to enable HTTPS, or set ACHERON_ALLOW_INSECURE=1 to silence this warning."
            )
        return {}
    cert, key = pair
    return {"ssl_certfile": cert, "ssl_keyfile": key}


def grpc_server_credentials() -> grpc.ServerCredentials | None:
    """Return gRPC server credentials if TLS is configured, else None."""
    pair = _require_pair()
    if pair is None:
        return None
    cert_path, key_path = pair
    cert_pem = Path(cert_path).read_bytes()
    key_pem = Path(key_path).read_bytes()
    return grpc.ssl_server_credentials([(key_pem, cert_pem)])


def resolve_ca_path() -> str | None:
    """Resolve the CA certificate path from environment variables.

    Reads ``ACHERON_TLS_CA_FILE`` first, then falls back to the standard
    ``SSL_CERT_FILE`` (honored by httpx and stdlib ``ssl``). Returns None
    when neither is set — callers decide whether to fall back to insecure
    or system trust.
    """
    return os.environ.get("ACHERON_TLS_CA_FILE") or os.environ.get("SSL_CERT_FILE") or None


def grpc_channel_credentials() -> grpc.ChannelCredentials | None:
    """Return gRPC channel credentials to verify a CA, or None.

    Reads ``ACHERON_TLS_CA_FILE`` first, then falls back to the standard
    ``SSL_CERT_FILE`` (honored by httpx and stdlib ``ssl``) so the orchestrator
    can use a single trust-store env var. If neither is set, returns None
    and callers should use an insecure channel.
    """
    ca = resolve_ca_path()
    if ca is None:
        return None
    ca_pem = Path(ca).read_bytes()
    return grpc.ssl_channel_credentials(root_certificates=ca_pem)


def grpc_channel(target: str, *, require_tls: bool = False) -> grpc.aio.Channel:
    """Return a gRPC channel, requiring CA verification for secure dispatch.

    ``require_tls`` is used for production worker dispatch. Local tests and
    explicitly opted-in development use ``ACHERON_ALLOW_INSECURE=1``.
    """
    if _allow_insecure():
        return grpc.aio.insecure_channel(
            target,
            options=(("grpc.max_receive_message_length", _GRPC_MAX_RECEIVE_MESSAGE_BYTES),),
        )

    creds = grpc_channel_credentials()
    if creds is None:
        if require_tls:
            raise RuntimeError("ACHERON_TLS_CA_FILE is required for authenticated gRPC dispatch")
        _LOG.warning(
            "ACHERON_TLS_CA_FILE is unset — opening insecure gRPC channel to %s. "
            "Set ACHERON_TLS_CA_FILE to enable verification, or "
            "set ACHERON_ALLOW_INSECURE=1 for local development.",
            target,
        )
        return grpc.aio.insecure_channel(
            target,
            options=(("grpc.max_receive_message_length", _GRPC_MAX_RECEIVE_MESSAGE_BYTES),),
        )
    return grpc.aio.secure_channel(
        target,
        creds,
        options=(("grpc.max_receive_message_length", _GRPC_MAX_RECEIVE_MESSAGE_BYTES),),
    )
