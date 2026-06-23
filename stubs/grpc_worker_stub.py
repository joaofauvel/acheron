"""Stub gRPC TTS worker for local development — streams canned PCM chunks."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING

import grpc
import grpc.aio
import httpx
import uvicorn
from fastapi import FastAPI
from grpc.health.v1 import health, health_pb2, health_pb2_grpc

from acheron.proto import synthesis_pb2, synthesis_pb2_grpc

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

_SILENT_PCM = b"\x00\x00" * 2205  # 100ms of silence at 22050 Hz, 16-bit mono


class _SynthesisServicer(synthesis_pb2_grpc.SynthesisServicer):
    """Returns canned silent PCM chunks."""

    def Synthesize(  # type: ignore[misc]  # noqa: N802
        self,
        request: synthesis_pb2.SynthesisRequest,  # type: ignore[name-defined]
        context: grpc.aio.ServicerContext,  # type: ignore[type-arg]
    ) -> AsyncIterator[synthesis_pb2.OutputChunk]:  # type: ignore[name-defined]
        for _ in range(3):
            yield synthesis_pb2.OutputChunk(  # type: ignore[attr-defined]
                pcm_data=_SILENT_PCM,
                sample_rate=22050,
                channels=1,
            )


def create_http_app() -> FastAPI:
    """Create the FastAPI app for the HTTP /health sidecar."""
    app = FastAPI(title="gRPC Stub Health")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def _grpc_endpoint(endpoint: str) -> str:
    """Strip any URL scheme from an endpoint so gRPC can use it as a channel target.

    gRPC's `insecure_channel` expects `host:port`, not `http://host:port`. Workers
    typically advertise their endpoint with a scheme for HTTP probes, so we strip
    it here before registering.
    """
    if endpoint.startswith("http://"):
        return endpoint[len("http://") :]
    if endpoint.startswith("https://"):
        return endpoint[len("https://") :]
    return endpoint


async def _register(endpoint: str, token: str) -> None:
    """Register with orchestrator, retrying until success."""
    orchestrator_url = os.environ.get("ORCHESTRATOR_URL", "http://orchestrator:8000")
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = {
        "worker_id": "tts-grpc-stub",
        "endpoint": _grpc_endpoint(endpoint),
        "transport": "grpc",
        "capabilities": {
            "worker_type": "tts",
            "supported_languages_in": ["en", "es", "fr", "de"],
            "supported_languages_out": ["en", "es", "fr", "de"],
            "metadata": {"stub": True, "transport": "grpc"},
        },
    }

    async with httpx.AsyncClient() as client:
        while True:
            try:
                health_resp = await client.get(f"{orchestrator_url}/health")
                health_resp.raise_for_status()
                resp = await client.post(
                    f"{orchestrator_url}/workers",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
            except (httpx.HTTPError, OSError) as exc:
                logger.debug("Orchestrator not ready (%s), retrying...", exc)
                await asyncio.sleep(1)
            else:
                logger.info("Registered tts-grpc-stub with orchestrator")
                return


async def create_server(port: int = 9001, *, register: bool = True) -> tuple[grpc.aio.Server, int]:
    """Create and optionally start the stub gRPC server."""
    from acheron.shell.tls import grpc_server_credentials

    creds = grpc_server_credentials()
    server = grpc.aio.server()
    synthesis_pb2_grpc.add_SynthesisServicer_to_server(_SynthesisServicer(), server)  # type: ignore[no-untyped-call]
    health_servicer = health.HealthServicer()
    health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    if creds is None:
        actual_port = server.add_insecure_port(f"0.0.0.0:{port}")
    else:
        actual_port = server.add_secure_port(f"0.0.0.0:{port}", creds)

    if register:
        endpoint = os.environ.get("WORKER_ENDPOINT", f"http://localhost:{actual_port}")
        token = os.environ.get("ACHERON_REGISTRATION_TOKEN", "")
        await _register(endpoint, token)

    return server, actual_port


async def _serve() -> None:
    """Run the stub gRPC server and the HTTP /health sidecar."""
    port = int(os.environ.get("WORKER_PORT", "9001"))
    http_port = int(os.environ.get("WORKER_HTTP_PORT", "9002"))

    server, actual_port = await create_server(port)
    await server.start()
    logger.info("gRPC stub worker listening on port %d", actual_port)

    http_app = create_http_app()
    config = uvicorn.Config(http_app, host="0.0.0.0", port=http_port, log_level="warning")
    http_server = uvicorn.Server(config)
    logger.info("HTTP /health sidecar listening on port %d", http_port)

    server_task = asyncio.create_task(server.wait_for_termination())
    http_task = asyncio.create_task(http_server.serve())
    try:
        await asyncio.gather(server_task, http_task)
    finally:
        await server.stop(0)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_serve())
