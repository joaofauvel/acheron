"""HTMX dashboard for the Acheron orchestrator."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request  # noqa: TC002

_LOGGER = logging.getLogger(__name__)
_TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")


def create_app(orchestrator_url: str | None = None) -> FastAPI:
    """Create the Acheron dashboard FastAPI application.

    Reads the orchestrator URL from the ``ACHERON_URL`` environment variable
    when not provided explicitly.
    """
    if orchestrator_url is None:
        orchestrator_url = os.environ.get("ACHERON_URL", "http://localhost:8000")
    app = FastAPI(title="Acheron Dashboard")

    async def _fetch(path: str) -> dict:
        try:
            async with httpx.AsyncClient(base_url=orchestrator_url) as client:
                resp = await client.get(path)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError, OSError:
            _LOGGER.warning("Dashboard cannot reach orchestrator at %s%s", orchestrator_url, path)
            return {}

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        # Only trust X-Forwarded-User when behind a reverse proxy that
        # authenticates and strips the header. Unauthenticated clients can
        # set this header to any value; no access decision depends on it.
        user = ""
        if os.environ.get("ACHERON_TRUST_REVERSE_PROXY") == "1":
            user = request.headers.get("X-Forwarded-User", "")
        return _TEMPLATES.TemplateResponse(request, "index.html", context={"user": user})

    @app.get("/partials/jobs", response_class=HTMLResponse)
    async def jobs_partial(request: Request) -> HTMLResponse:
        data = await _fetch("/jobs")
        return _TEMPLATES.TemplateResponse(request, "partials/jobs.html", context={"jobs": data.get("jobs", [])})

    @app.get("/partials/workers", response_class=HTMLResponse)
    async def workers_partial(request: Request) -> HTMLResponse:
        data = await _fetch("/workers")
        return _TEMPLATES.TemplateResponse(
            request, "partials/workers.html", context={"workers": data.get("workers", [])}
        )

    @app.get("/partials/cost", response_class=HTMLResponse)
    async def cost_partial(request: Request) -> HTMLResponse:
        data = await _fetch("/jobs")
        return _TEMPLATES.TemplateResponse(request, "partials/cost.html", context={"jobs": data.get("jobs", [])})

    @app.get("/partials/status", response_class=HTMLResponse)
    async def status_partial(request: Request) -> HTMLResponse:  # noqa: ARG001
        """Proxy the orchestrator's status partial; show Disconnected on failure."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{orchestrator_url}/partials/status", timeout=5.0)
                resp.raise_for_status()
                return HTMLResponse(resp.text)
        except (httpx.HTTPError, OSError):
            return HTMLResponse('<span class="dot dot-red"></span> Disconnected')

    return app
