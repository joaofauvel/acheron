"""RunPod Serverless entrypoint — loads the model eagerly at boot, then calls runpod.serverless.start.

RunPod schedules GPU pods on demand; the entry loads the model into VRAM
before the first inference request arrives so warm pods respond immediately
and cold pods pay the load cost once.
"""

from __future__ import annotations

import asyncio
import logging

import runpod

from acheron.worker_sdk.cloud import make_runpod_handler
from acheron.worker_sdk.config_loader import load_settings
from workers.qwen3tts.handler import Qwen3TTSRunpodHandler

logging.basicConfig(level=logging.INFO)
# Third-party loggers are chatty; keep the cloud-side output clean for the
# RunPod dashboard tail. Errors and warnings still surface.
logging.getLogger("transformers").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def main() -> None:
    """Boot the RunPod serverless worker: load model, then serve."""
    # The RunPod runtime image has no mounted worker.yaml; env drives config.
    # The CLI's discovery falls back to env vars when no YAML is present.
    settings = load_settings()
    handler = Qwen3TTSRunpodHandler(settings)
    asyncio.run(handler.startup())  # eager model load
    runpod.serverless.start({"handler": make_runpod_handler(handler)})


if __name__ == "__main__":
    main()
