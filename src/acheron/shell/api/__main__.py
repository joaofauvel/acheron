"""Orchestrator entry point: serve the FastAPI app via uvicorn, with optional TLS."""

from __future__ import annotations

import argparse

from acheron.shell.api.app import create_app
from acheron.worker_sdk._server import run_worker_server


def main() -> None:
    """Run the Acheron orchestrator via uvicorn."""
    parser = argparse.ArgumentParser(description="Run the Acheron orchestrator.")
    parser.add_argument("--host", default="0.0.0.0")  # noqa: S104  # bind all interfaces for docker
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    run_worker_server(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
