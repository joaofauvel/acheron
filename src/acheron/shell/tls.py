"""Backwards-compat shim — TLS helpers live in :mod:`acheron.tls` now.

Both the orchestrator and the worker SDK need TLS configuration. To
avoid a ``acheron.shell`` -> ``acheron.worker_sdk`` import cycle, the
helpers were moved to the top-level :mod:`acheron.tls` module. This
file re-exports them so existing import sites (``from acheron.shell.tls
import ...``) keep working.
"""

from acheron.tls import (
    grpc_channel,
    grpc_channel_credentials,
    grpc_server_credentials,
    resolve_ca_path,
    uvicorn_ssl_kwargs,
)

__all__ = [
    "grpc_channel",
    "grpc_channel_credentials",
    "grpc_server_credentials",
    "resolve_ca_path",
    "uvicorn_ssl_kwargs",
]
