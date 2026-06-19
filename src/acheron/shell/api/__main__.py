"""Orchestrator entry point: serve the FastAPI app via uvicorn, with optional TLS."""

from __future__ import annotations

import argparse

import uvicorn

from acheron.shell.api.app import create_app
from acheron.shell.tls import uvicorn_ssl_kwargs


def main() -> None:
    """Run the Acheron orchestrator via uvicorn."""
    parser = argparse.ArgumentParser(description="Run the Acheron orchestrator.")
    parser.add_argument("--host", default="0.0.0.0")  # noqa: S104  # bind all interfaces for docker
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    ssl = uvicorn_ssl_kwargs()
    uvicorn.run(
        create_app(),
        host=args.host,
        port=args.port,
        ssl_certfile=ssl.get("ssl_certfile"),
        ssl_keyfile=ssl.get("ssl_keyfile"),
    )


if __name__ == "__main__":
    main()
