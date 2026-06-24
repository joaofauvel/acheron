"""TTS stub — HTTP edge, multipart output, local price=zero."""

import os

import uvicorn

from acheron.tls import uvicorn_ssl_kwargs
from acheron.worker_sdk import create_worker_app
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
    ssl = uvicorn_ssl_kwargs()
    uvicorn.run(
        app,
        host=settings.listen_host,
        port=settings.listen_port,
        ssl_certfile=ssl.get("ssl_certfile"),
        ssl_keyfile=ssl.get("ssl_keyfile"),
    )


if __name__ == "__main__":
    main()
