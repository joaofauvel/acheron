"""In-process HTTP server that speaks RunPod's /run + /status protocol for stubs."""

from __future__ import annotations

import threading
from typing import Any

from fastapi import FastAPI


def make_mock_runpod_app(artifacts_response: dict[str, Any]) -> FastAPI:
    """Build a FastAPI app that mimics RunPod's /run + /status endpoints.

    The /run handler echoes the submitted job's id in the response so the
    SDK's poll-by-id flow (or a future forwarder) can correlate the
    response with the request. The /status/{id} endpoint always reports
    COMPLETED.
    """
    app = FastAPI(title="Mock RunPod Serverless")

    @app.post("/run")
    async def run(body: dict[str, Any]) -> dict[str, Any]:
        # Echo the submitted job id so callers can correlate. The body shape
        # mirrors what the SDK sends: {"input": {"job_id": ..., ...}}.
        submitted_id = str(body.get("input", {}).get("job_id", "stub-job-1"))
        return {
            "id": submitted_id,
            "status": "COMPLETED",
            "output": artifacts_response,
        }

    @app.get("/status/{job_id}")
    async def status(job_id: str) -> dict[str, str]:
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
