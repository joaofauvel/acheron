"""Shared uvicorn server runner for the orchestrator and worker edge entry points."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import uvicorn

from acheron.tls import uvicorn_ssl_kwargs

if TYPE_CHECKING:
    import ssl

    from fastapi import FastAPI

logger = logging.getLogger(__name__)


def run_worker_server(
    app: FastAPI,
    *,
    host: str,
    port: int,
    ssl_ctx: ssl.SSLContext | None = None,
) -> None:
    """Build a uvicorn Config+Server and run it (with optional TLS).

    When ``ssl_ctx`` is provided, it is installed via ``Config.ssl_context_factory``.
    Otherwise, the env-var-driven ``ACHERON_TLS_CERT_FILE`` / ``ACHERON_TLS_KEY_FILE``
    pair is read through :func:`acheron.tls.uvicorn_ssl_kwargs` and passed as
    ``ssl_certfile`` / ``ssl_keyfile`` (or omitted entirely when neither is set).
    """
    config_kwargs: dict[str, Any] = {"host": host, "port": port}
    if ssl_ctx is not None:
        config_kwargs["ssl_context_factory"] = lambda _config, _default: ssl_ctx
    else:
        ssl_kwargs = uvicorn_ssl_kwargs()
        config_kwargs["ssl_certfile"] = ssl_kwargs.get("ssl_certfile")
        config_kwargs["ssl_keyfile"] = ssl_kwargs.get("ssl_keyfile")
    config = uvicorn.Config(app, **config_kwargs)
    uvicorn.Server(config).run()


__all__ = ["run_worker_server"]
