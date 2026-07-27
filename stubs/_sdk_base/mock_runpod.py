"""In-process HTTP server that speaks RunPod's /run + /status protocol for stubs.

Phase 3a extensions (per docs/ux_review/SPEC.md §7.6):
- POST /graphql serves the two RunPod pricing queries (see pricing.py:202-231).
- POST /_admin/control sets toggles (cold_start_ms, pricing_api_down, etc.).
- GET /_admin/runs returns the last N /run records.
- POST /_admin/reset clears all toggles and runs.
- GET /endpoints/{id} returns 200 (for RunPodHealthProvider).
- POST /run honors cold_start_ms and fail_next_n.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

_DEFAULT_ENDPOINTS: dict[str, dict[str, Any]] = {
    "abc12345": {"gpu_id": "NVIDIA A40", "secure_low": 2.49, "community_low": 0.69},
    "qwen-edge": {"gpu_id": "NVIDIA L4", "secure_low": 1.39, "community_low": 0.69},
}


def make_mock_runpod_app(artifacts_response: dict[str, Any]) -> FastAPI:
    """Build a FastAPI app that mimics RunPod's /run + /status endpoints.

    The /run handler echoes the submitted job's id in the response so the
    SDK's poll-by-id flow (or a future forwarder) can correlate the
    response with the request. The /status/{id} endpoint always reports
    COMPLETED.
    """
    app = FastAPI(title="Mock RunPod Serverless")

    state: dict[str, Any] = {
        "endpoints": {k: dict(v) for k, v in _DEFAULT_ENDPOINTS.items()},
        "toggles": {
            "cold_start_ms": 0,
            "pricing_api_down": False,
            "endpoint_disabled": set(),
            "fail_next_n": 0,
        },
        "runs": [],
    }

    def _record_run(body: dict[str, Any], status: str, duration_ms: float) -> None:
        record = {
            "ts": time.time(),
            "endpoint_id": body.get("endpoint_id", "unknown"),
            "payload": body,
            "status": status,
            "duration_ms": duration_ms,
        }
        state["runs"].append(record)
        if len(state["runs"]) > 100:
            del state["runs"][:-100]

    @app.post("/run", response_model=None)
    async def run(body: dict[str, Any]) -> JSONResponse | dict[str, Any]:
        toggles = state["toggles"]
        cold = int(toggles["cold_start_ms"])
        if cold > 0:
            time.sleep(cold / 1000.0)
        if toggles["fail_next_n"] > 0:
            toggles["fail_next_n"] -= 1
            _record_run(body, "FAILED", float(cold))
            return JSONResponse({"error": "simulated failure", "status": "FAILED"}, status_code=500)
        endpoint_id = str(body.get("endpoint_id", "stub"))
        if endpoint_id in toggles["endpoint_disabled"]:
            return JSONResponse({"error": "endpoint disabled"}, status_code=404)
        submitted_id = str(body.get("input", {}).get("job_id", "stub-job-1"))
        _record_run(body, "COMPLETED", float(cold))
        return {
            "id": submitted_id,
            "status": "COMPLETED",
            "output": artifacts_response,
        }

    @app.get("/status/{job_id}")
    async def status(job_id: str) -> dict[str, str]:
        return {"status": "COMPLETED"}

    @app.post("/graphql", response_model=None)
    async def graphql(request: Request) -> JSONResponse | dict[str, Any]:
        if state["toggles"]["pricing_api_down"]:
            return JSONResponse(
                {"errors": [{"message": "simulated pricing API outage"}]},
                status_code=500,
            )
        body = await request.json()
        query = str(body.get("query", ""))
        variables = body.get("variables", {}) or {}
        if "myself" in query and "endpoints" in query:
            return {
                "data": {
                    "myself": {
                        "endpoints": [{"id": eid, "gpuIds": cfg["gpu_id"]} for eid, cfg in state["endpoints"].items()]
                    }
                }
            }
        if "gpuTypes" in query:
            gpu_id = str(variables.get("id", ""))
            secure = bool(variables.get("secure", False))
            for cfg in state["endpoints"].values():
                if cfg["gpu_id"] == gpu_id:
                    price = cfg["secure_low"] if secure else cfg["community_low"]
                    return {"data": {"gpuTypes": [{"lowestPrice": {"uninterruptablePrice": price}}]}}
            return {"data": {"gpuTypes": []}}
        return {"data": {}}

    @app.post("/_admin/control")
    async def admin_control(request: Request) -> dict[str, Any]:
        body = await request.json()
        toggle = body.get("toggle")
        value = body.get("value")
        toggles = state["toggles"]
        if toggle == "cold_start_ms":
            toggles["cold_start_ms"] = int(value)
        elif toggle == "pricing_api_down":
            toggles["pricing_api_down"] = bool(value)
        elif toggle == "endpoint_disabled":
            toggles["endpoint_disabled"] = {str(v) for v in (value or [])}
        elif toggle == "fail_next_n":
            toggles["fail_next_n"] = int(value)
        else:
            return {"ok": False, "error": f"unknown toggle: {toggle}"}
        return {"ok": True, "toggle": toggle, "value": value}

    @app.post("/_admin/reset")
    async def admin_reset() -> dict[str, Any]:
        state["toggles"] = {
            "cold_start_ms": 0,
            "pricing_api_down": False,
            "endpoint_disabled": set(),
            "fail_next_n": 0,
        }
        state["endpoints"] = {k: dict(v) for k, v in _DEFAULT_ENDPOINTS.items()}
        state["runs"] = []
        return {"ok": True}

    @app.get("/_admin/runs")
    async def admin_runs() -> dict[str, Any]:
        return {"runs": list(state["runs"])}

    @app.get("/endpoints/{endpoint_id}", response_model=None)
    async def endpoint_health(endpoint_id: str) -> JSONResponse | dict[str, Any]:
        if endpoint_id in state["toggles"]["endpoint_disabled"]:
            return JSONResponse({"error": "not found"}, status_code=404)
        if endpoint_id not in state["endpoints"]:
            return JSONResponse({"error": "not found"}, status_code=404)
        cfg = state["endpoints"][endpoint_id]
        return {"id": endpoint_id, "status": "ready", "gpu_id": cfg["gpu_id"]}

    @app.patch("/endpoints/{endpoint_id}", response_model=None)
    async def endpoint_patch(endpoint_id: str, request: Request) -> JSONResponse | dict[str, str]:
        if endpoint_id in state["toggles"]["endpoint_disabled"]:
            return JSONResponse({"error": "not found"}, status_code=404)
        if endpoint_id not in state["endpoints"]:
            return JSONResponse({"error": "not found"}, status_code=404)
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "PATCH body must be a JSON object"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "PATCH body must be a JSON object"}, status_code=400)
        gpu_id = body.get("gpu_id")
        if not isinstance(gpu_id, str):
            return JSONResponse({"error": "gpu_id must be a string"}, status_code=400)
        state["endpoints"][endpoint_id]["gpu_id"] = gpu_id
        return {"id": endpoint_id, "gpu_id": gpu_id}

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
