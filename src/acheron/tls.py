"""TLS helpers — env-var to SSL credentials conversion for HTTP and gRPC.

Lives at the top level of ``acheron`` (not under ``shell``) so both the
orchestrator and the worker SDK can depend on it without violating the
``worker-sdk-no-shell`` import-linter contract.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import grpc
import grpc.aio

from acheron.core.errors import AcheronError

_LOG = logging.getLogger(__name__)


def _allow_insecure() -> bool:
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


def grpc_channel(target: str) -> grpc.aio.Channel:
    """Return a gRPC channel to `target`.

    Uses `secure_channel` with the configured CA if `ACHERON_TLS_CA_FILE` is set,
    else `insecure_channel`. The target is expected to be `host:port` (no scheme).
    Logs a WARNING on insecure fallback unless `ACHERON_ALLOW_INSECURE=1` is set.
    """
    creds = grpc_channel_credentials()
    if creds is None:
        if not _allow_insecure():
            _LOG.warning(
                "ACHERON_TLS_CA_FILE is unset — opening insecure gRPC channel to %s. "
                "Set ACHERON_TLS_CA_FILE to enable verification, or "
                "set ACHERON_ALLOW_INSECURE=1 to silence this warning.",
                target,
            )
        return grpc.aio.insecure_channel(target)
    return grpc.aio.secure_channel(target, creds)
