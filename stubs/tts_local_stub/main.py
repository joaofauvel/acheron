"""TTS stub — HTTP edge, multipart output, local price=zero."""

import uvicorn

from acheron.worker_sdk import create_worker_app
from acheron.worker_sdk.config_loader import load_settings
from stubs._sdk_base import StubTTSHandler


def main() -> None:
    settings = load_settings()
    handler = StubTTSHandler(settings)
    app = create_worker_app(handler=handler, settings=settings)
    uvicorn.run(app, host=settings.listen_host, port=settings.listen_port)


if __name__ == "__main__":
    main()
