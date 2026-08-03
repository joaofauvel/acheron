"""Orchestrator entry point: serve the FastAPI app via uvicorn, with optional TLS."""

from __future__ import annotations

import argparse

from acheron.shell.api.app import create_app
from acheron.tls import CertificateManager
from acheron.worker_sdk._server import run_worker_server


def main() -> None:
    """Run the Acheron orchestrator via uvicorn."""
    parser = argparse.ArgumentParser(description="Run the Acheron orchestrator.")
    parser.add_argument("--host", default="0.0.0.0")  # noqa: S104  # bind all interfaces for docker
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    certificate_manager = CertificateManager.from_env()
    app = create_app(certificate_manager=certificate_manager)
    ssl_ctx = certificate_manager.ssl_context if certificate_manager is not None else None
    run_worker_server(app, host=args.host, port=args.port, ssl_ctx=ssl_ctx)


if __name__ == "__main__":
    main()
