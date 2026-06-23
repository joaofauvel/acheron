"""In-process HTTP server that speaks RunPod's /run + /status protocol for stubs."""

from __future__ import annotations

import threading
from typing import Any

from fastapi import FastAPI


def make_mock_runpod_app(artifacts_response: dict[str, Any]) -> FastAPI:
    app = FastAPI(title="Mock RunPod Serverless")

    @app.post("/run")
    async def run(body: dict[str, Any]) -> dict[str, Any]:
        return {"id": "stub-job-1", "status": "COMPLETED", "output": artifacts_response}

    @app.get("/status/{job_id}")
    async def status(job_id: str) -> dict[str, Any]:
        return {"status": "COMPLETED"}

    return app


def start_mock_runpod_in_thread(port: int, artifacts_response: dict[str, Any]) -> Any:
    """Start a mock RunPod endpoint on 127.0.0.1:port in a daemon thread."""
    import uvicorn

    app = make_mock_runpod_app(artifacts_response)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server
