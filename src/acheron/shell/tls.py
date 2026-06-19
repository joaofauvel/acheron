"""TLS helpers — env-var to SSL credentials conversion for HTTP and gRPC."""

from __future__ import annotations

import os
from pathlib import Path

import grpc
import grpc.aio

from acheron.core.errors import AcheronError


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


def uvicorn_ssl_kwargs() -> dict[str, object]:
    """Return uvicorn kwargs to enable TLS, or `{}` if TLS is not configured.

    Both `ACHERON_TLS_CERT_FILE` and `ACHERON_TLS_KEY_FILE` must be set together.
    """
    pair = _require_pair()
    if pair is None:
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


def grpc_channel_credentials() -> grpc.ChannelCredentials | None:
    """Return gRPC channel credentials to verify a CA, or None.

    Reads `ACHERON_TLS_CA_FILE`. If unset, returns None and callers should use
    an insecure channel.
    """
    ca = os.environ.get("ACHERON_TLS_CA_FILE")
    if ca is None:
        return None
    ca_pem = Path(ca).read_bytes()
    return grpc.ssl_channel_credentials(root_certificates=ca_pem)


def grpc_channel(target: str) -> grpc.aio.Channel:
    """Return a gRPC channel to `target`.

    Uses `secure_channel` with the configured CA if `ACHERON_TLS_CA_FILE` is set,
    else `insecure_channel`. The target is expected to be `host:port` (no scheme).
    """
    creds = grpc_channel_credentials()
    if creds is None:
        return grpc.aio.insecure_channel(target)
    return grpc.aio.secure_channel(target, creds)
