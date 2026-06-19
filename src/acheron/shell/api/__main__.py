"""Orchestrator entry point: serve the FastAPI app via uvicorn, with optional TLS."""

from __future__ import annotations

import argparse

import uvicorn

from acheron.shell.api.app import create_app
from acheron.shell.tls import uvicorn_ssl_kwargs


def main() -> None:
    """Run the Acheron orchestrator via uvicorn."""
    parser = argparse.ArgumentParser(description="Run the Acheron orchestrator.")
    parser.add_argument("--host", default="0.0.0.0")  # noqa: S104
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(
        create_app(),
        host=args.host,
        port=args.port,
        **uvicorn_ssl_kwargs(),  # type: ignore[arg-type]
        # uvicorn.run has many typed params; a dict[str, object] can't precisely
        # match each one in mypy's strict mode. The values are actually well-typed
        # strings (cert path, key path) or absent.
    )


if __name__ == "__main__":
    main()
