"""TTS stub — HTTP edge, multipart output, local price=zero.

The gRPC stub's worker-edge half stays HTTP — Plan 2 ships Artifact-mode
OutputChunk on the proto side and the existing test_grpc_worker.py covers
the gRPC path. This stub keeps the HTTP-edge side alive for compose-level
healthcheck / registration parity.
"""

import os

from acheron.worker_sdk import create_worker_app
from acheron.worker_sdk._server import run_worker_server
from acheron.worker_sdk.config_loader import load_settings
from stubs._sdk_base import StubTTSHandler


def main() -> None:
    settings = load_settings()
    handler = StubTTSHandler(settings)
    app = create_worker_app(
        handler=handler,
        settings=settings,
        disable_registration=os.environ.get("ACHERON_DISABLE_REGISTRATION") == "1",
    )
    run_worker_server(app, host=settings.listen_host, port=settings.listen_port)


if __name__ == "__main__":
    main()
