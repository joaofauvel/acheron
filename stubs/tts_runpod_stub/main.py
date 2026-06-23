"""TTS stub — RunPod edge with in-process mocked RunPod endpoint."""

import os

import uvicorn

from acheron.worker_sdk import create_worker_app
from acheron.worker_sdk.config_loader import load_settings
from stubs._sdk_base import StubTTSHandler
from stubs._sdk_base.mock_runpod import start_mock_runpod_in_thread


def main() -> None:
    os.environ.setdefault("ACHERON_WORKER__RUNPOD_BASE_URL", "http://127.0.0.1:8999")
    # Start the mock RunPod endpoint before the edge registers.
    start_mock_runpod_in_thread(
        port=8999,
        artifacts_response={"artifacts": [{"filename": "out.wav", "data": "AAEC"}]},
    )
    settings = load_settings()
    handler = StubTTSHandler(settings)
    app = create_worker_app(handler=handler, settings=settings)
    uvicorn.run(app, host=settings.listen_host, port=settings.listen_port)


if __name__ == "__main__":
    main()
