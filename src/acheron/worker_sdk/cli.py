"""``acheron-worker-edge`` is the image's CMD module, not a user-facing CLI.

The deployer configures the edge container via ``docker-compose.yml``
service env vars + ``worker.yaml`` discovery — they never invoke this
binary directly. It exists so the same published generic image serves
TTS / ASR / translation RunPod workers (only the handler import path +
``worker.yaml`` differ per service).

Usage (in the published image's CMD):
    python -m acheron.worker_sdk.cli --handler workers.qwen3tts.handler:Qwen3TTSRunpodHandler
"""

from __future__ import annotations

import argparse
import importlib
import logging
import os
import sys
from typing import Any

from acheron.worker_sdk._server import run_worker_server
from acheron.worker_sdk.app import create_worker_app
from acheron.worker_sdk.config_loader import load_settings

logger = logging.getLogger(__name__)


def _import_handler(import_path: str) -> type[Any]:
    """Resolve ``"pkg.mod:ClassName"`` to the class object."""
    if ":" not in import_path:
        msg = f"Handler import path must be 'module:Class' (got {import_path!r})"
        raise ValueError(msg)
    module_name, _, class_name = import_path.partition(":")
    module = importlib.import_module(module_name)
    try:
        cls: type[Any] = getattr(module, class_name)
    except AttributeError as exc:
        msg = f"Module {module_name!r} has no attribute {class_name!r}"
        raise AttributeError(msg) from exc
    return cls


def main() -> None:
    """Load the worker handler, build the edge FastAPI app, and serve uvicorn."""
    parser = argparse.ArgumentParser(description="acheron-worker-edge image entrypoint")
    parser.add_argument("--handler", required=True, help="Dotted path pkg.mod:ClassName")
    parser.add_argument("--config", default=None, help="Path to worker YAML (overrides discovery)")
    args = parser.parse_args()

    if args.config:
        os.environ["WORKER_CONFIG"] = args.config

    settings = load_settings()

    if not settings.handler:
        msg = "Handler import path missing — set 'handler' in worker.yaml or pass --handler"
        raise SystemExit(msg)

    handler_class = _import_handler(settings.handler)
    phantom_class: type[Any] | None = None
    if settings.phantom_handler:
        phantom_class = _import_handler(settings.phantom_handler)
    if phantom_class is not None:
        handler = handler_class(settings, phantom_handler=phantom_class)
    else:
        handler = handler_class(settings)
    app = create_worker_app(handler=handler, settings=settings)

    logging.basicConfig(
        level=settings.log_level,
        stream=sys.stdout,
    )
    run_worker_server(app, host=settings.listen_host, port=settings.listen_port)


if __name__ == "__main__":
    main()
