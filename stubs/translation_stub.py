"""Stub translation worker — returns mock translated text."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import FastAPI

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)


def _get_config() -> dict[str, str]:
    return {
        "worker_type": os.environ.get("WORKER_TYPE", "TRANSLATION"),
        "worker_endpoint": os.environ.get("WORKER_ENDPOINT", "http://localhost:8003"),
        "orchestrator_url": os.environ.get("ORCHESTRATOR_URL", "http://orchestrator:8000"),
        "registration_token": os.environ.get("ACHERON_REGISTRATION_TOKEN", ""),
    }


async def _register(cfg: dict[str, str]) -> None:
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
    cfg = _get_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await _register(cfg)
        yield

    app = FastAPI(title="Translation Stub Worker", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/submit")
    async def submit(body: dict[str, Any]) -> dict[str, Any]:
        text = body.get("payload", {}).get("text", "")
        src = body.get("payload", {}).get("source_language", "en")
        dst = body.get("payload", {}).get("target_language", "es")
        translated = f"{text} [translated {src}→{dst}]"
        return {"status": "completed", "output_data": translated}

    @app.post("/execute")
    async def execute(body: dict[str, Any]) -> dict[str, Any]:
        job_id = body.get("job_id", "unknown")
        text = body.get("payload", {}).get("text", "")
        src = body.get("payload", {}).get("source_language", "en")
        dst = body.get("payload", {}).get("target_language", "es")
        translated = f"{text} [translated {src}→{dst}]"
        return {
            "job_id": job_id,
            "status": "success",
            "outputs": [
                {
                    "path": f"{job_id}.txt",
                    "filename": f"{job_id}.txt",
                    "size_bytes": len(translated),
                    "checksum": "",
                    "content_type": "text/plain",
                }
            ],
            "metrics": {"duration_seconds": 0.01},
            "error": None,
        }

    return app
