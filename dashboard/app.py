"""HTMX dashboard for the Acheron orchestrator."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

if TYPE_CHECKING:
    from starlette.requests import Request

_TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")


def create_app(orchestrator_url: str = "http://localhost:8000") -> FastAPI:
    """Create the Acheron dashboard FastAPI application."""
    app = FastAPI(title="Acheron Dashboard")

    async def _fetch(path: str) -> dict:
        async with httpx.AsyncClient(base_url=orchestrator_url) as client:
            resp = await client.get(path)
            resp.raise_for_status()
            return resp.json()

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        user = request.headers.get("X-Forwarded-User", "")
        return _TEMPLATES.TemplateResponse(request, "index.html", context={"user": user})

    @app.get("/partials/jobs", response_class=HTMLResponse)
    async def jobs_partial(request: Request) -> HTMLResponse:
        data = await _fetch("/jobs")
        return _TEMPLATES.TemplateResponse(request, "partials/jobs.html", context={"jobs": data["jobs"]})

    @app.get("/partials/workers", response_class=HTMLResponse)
    async def workers_partial(request: Request) -> HTMLResponse:
        data = await _fetch("/workers")
        return _TEMPLATES.TemplateResponse(request, "partials/workers.html", context={"workers": data["workers"]})

    @app.get("/partials/cost", response_class=HTMLResponse)
    async def cost_partial(request: Request) -> HTMLResponse:
        data = await _fetch("/jobs")
        return _TEMPLATES.TemplateResponse(request, "partials/cost.html", context={"jobs": data["jobs"]})

    return app
