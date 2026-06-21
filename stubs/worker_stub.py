"""Stub worker for local development — returns instant mock results."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import struct
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import FastAPI

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)


def _silent_wav(duration_ms: int = 100, sample_rate: int = 22050) -> bytes:
    """Generate a minimal valid WAV file with silence."""
    num_samples = int(sample_rate * duration_ms / 1000)
    data_size = num_samples * 2
    return (
        b"RIFF"
        + struct.pack("<I", 36 + data_size)
        + b"WAVE"
        + b"fmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16)
        + b"data"
        + struct.pack("<I", data_size)
        + b"\x00" * data_size
    )


def _get_config() -> dict[str, str]:
    """Read configuration from environment."""
    return {
        "worker_type": os.environ["WORKER_TYPE"],
        "worker_endpoint": os.environ["WORKER_ENDPOINT"],
        "orchestrator_url": os.environ["ORCHESTRATOR_URL"],
        "worker_port": os.environ.get("WORKER_PORT", "8001"),
        "registration_token": os.environ.get("ACHERON_REGISTRATION_TOKEN", ""),
    }


async def _register(cfg: dict[str, str]) -> None:
    """Register with orchestrator, retrying until success."""
    worker_type = cfg["worker_type"].lower()
    worker_id = f"{worker_type}-stub"
    headers: dict[str, str] = {}
    if cfg["registration_token"]:
        headers["Authorization"] = f"Bearer {cfg['registration_token']}"

    payload = {
        "worker_id": worker_id,
        "endpoint": cfg["worker_endpoint"],
        "transport": "http",
        "capabilities": {
            "worker_type": worker_type,
            "supported_languages_in": ["en", "es", "fr", "de"],
            "supported_languages_out": ["en", "es", "fr", "de"],
            "metadata": {"stub": True},
        },
    }

    async with httpx.AsyncClient() as client:
        while True:
            try:
                health_resp = await client.get(f"{cfg['orchestrator_url']}/health")
                health_resp.raise_for_status()
                resp = await client.post(
                    f"{cfg['orchestrator_url']}/workers",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
            except (httpx.HTTPError, OSError) as exc:
                logger.debug("Orchestrator not ready (%s), retrying...", exc)
                await asyncio.sleep(1)
            else:
                logger.info("Registered %s with orchestrator", worker_id)
                return


def create_app() -> FastAPI:
    """Create the stub worker FastAPI app."""
    cfg = _get_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await _register(cfg)
        yield

    app = FastAPI(title=f"{cfg['worker_type']} Stub Worker", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/submit")
    async def submit(body: dict[str, Any]) -> dict[str, Any]:
        if cfg["worker_type"] == "TTS":
            audio = _silent_wav()
            return {"status": "completed", "output_data": base64.b64encode(audio).decode()}
        return {"status": "completed", "output_data": "mock transcription"}

    @app.post("/execute")
    async def execute(body: dict[str, Any]) -> dict[str, Any]:
        job_id = body.get("job_id", "unknown")
        plan_job_id = job_id.rsplit("-", 1)[0]
        data_dir = Path(os.environ.get("ACHERON_DATA_DIR", "/data/jobs"))
        if cfg["worker_type"] == "TTS":
            audio = _silent_wav()
            step_dir = data_dir / plan_job_id / "synthesize"
            step_dir.mkdir(parents=True, exist_ok=True)
            out_path = step_dir / f"{job_id}.wav"
            out_path.write_bytes(audio)
            return {
                "job_id": job_id,
                "status": "success",
                "outputs": [
                    {
                        "path": str(out_path),
                        "filename": f"{job_id}.wav",
                        "size_bytes": len(audio),
                        "checksum": "",
                        "content_type": "audio/wav",
                    }
                ],
                "metrics": {"duration_seconds": 0.01},
                "error": None,
            }
        step_dir = data_dir / plan_job_id / "translate"
        step_dir.mkdir(parents=True, exist_ok=True)
        out_path = step_dir / f"{job_id}.txt"
        out_path.write_text("mock translated text", encoding="utf-8")
        return {
            "job_id": job_id,
            "status": "success",
            "outputs": [
                {
                    "path": str(out_path),
                    "filename": f"{job_id}.txt",
                    "size_bytes": out_path.stat().st_size,
                    "checksum": "",
                    "content_type": "text/plain",
                }
            ],
            "metrics": {"duration_seconds": 0.01},
            "error": None,
        }

    return app


def main() -> None:
    """Run the stub worker via uvicorn, with optional TLS."""
    import uvicorn

    from acheron.shell.tls import uvicorn_ssl_kwargs

    port = int(os.environ.get("WORKER_PORT", "8001"))
    ssl = uvicorn_ssl_kwargs()
    uvicorn.run(
        create_app(),
        host="0.0.0.0",
        port=port,
        ssl_certfile=ssl.get("ssl_certfile"),
        ssl_keyfile=ssl.get("ssl_keyfile"),
    )


if __name__ == "__main__":
    main()
