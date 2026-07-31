"""HTMX dashboard for the Acheron orchestrator."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from urllib.parse import quote, urlencode

import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request  # noqa: TC002

from dashboard.booting_progress import clamp_booting_elapsed, format_booting_elapsed

_LOGGER = logging.getLogger(__name__)
_SECONDS_PER_MINUTE = 60
_MINUTES_PER_HOUR = 60
_HOURS_PER_DAY = 24
_TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")
_TEMPLATES.env.globals.update(
    clamp_booting_elapsed=clamp_booting_elapsed,
    format_booting_elapsed=format_booting_elapsed,
)
_TEMPLATES.env.filters["urlencode"] = lambda value: quote(str(value), safe="")


def _format_age(timestamp: object) -> str:
    """Render a lifecycle timestamp as a compact age label."""
    try:
        parsed = datetime.fromisoformat(str(timestamp))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return "unknown"
    except ValueError:
        return "unknown"
    seconds = max(0.0, (datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds())
    if seconds < _SECONDS_PER_MINUTE:
        return f"{seconds:.0f}s"
    minutes = seconds / _SECONDS_PER_MINUTE
    if minutes < _MINUTES_PER_HOUR:
        return f"{minutes:.0f}m"
    hours = minutes / _MINUTES_PER_HOUR
    if hours < _HOURS_PER_DAY:
        return f"{hours:.1f}h"
    return f"{hours / _HOURS_PER_DAY:.1f}d"


_TEMPLATES.env.globals["format_age"] = _format_age


async def _fetch_orchestrator(orchestrator_url: str, path: str) -> dict[str, object]:
    """GET ``path`` from the orchestrator; return ``{}`` on any fetch failure."""
    try:
        async with httpx.AsyncClient(base_url=orchestrator_url) as client:
            resp = await client.get(path)
            resp.raise_for_status()
            payload = resp.json()
            return cast("dict[str, object]", payload)
    except Exception:  # noqa: BLE001
        _LOGGER.warning("Dashboard cannot reach orchestrator at %s%s", orchestrator_url, path)
        return {}


async def _proxy_status_partial(orchestrator_url: str) -> HTMLResponse:
    """Fetch the orchestrator's status partial; render Disconnected on failure."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{orchestrator_url}/partials/status", timeout=5.0)
            resp.raise_for_status()
            return HTMLResponse(resp.text)
    except httpx.HTTPError, OSError:
        return HTMLResponse('<span class="dot dot-red"></span> Disconnected')


def _jobs_path(request: Request) -> str:
    """Forward only supported job-list filters to the orchestrator."""
    keys = ("status", "since", "before", "older_than_seconds", "include_archived", "label")
    params = [(key, value) for key in keys if (value := request.query_params.get(key))]
    return f"/jobs?{urlencode(params)}" if params else "/jobs"


async def _job_detail_partial(orchestrator_url: str, request: Request, job_id: str) -> HTMLResponse:
    """Render a job detail fragment from the orchestrator response."""
    data = await _fetch_orchestrator(orchestrator_url, f"/jobs/{quote(job_id, safe='')}")
    return _TEMPLATES.TemplateResponse(
        request,
        "partials/job_detail.html",
        context={"job": data, "orchestrator_url": orchestrator_url},
    )


async def _cost_partial(orchestrator_url: str, request: Request) -> HTMLResponse:
    """Render cost windows and aggregate execution-time estimates."""
    window = request.query_params.get("window", "7d")
    if window not in {"24h", "7d", "30d", "all"}:
        window = "7d"
    summary = await _fetch_orchestrator(orchestrator_url, f"/cost?window={window}")
    jobs_data = await _fetch_orchestrator(orchestrator_url, "/jobs")
    jobs = cast("list[dict[str, object]]", jobs_data.get("jobs", []))
    return _TEMPLATES.TemplateResponse(
        request,
        "partials/cost.html",
        context={"jobs": jobs, "summary": summary, "window": window},
    )


def create_app(orchestrator_url: str | None = None) -> FastAPI:
    """Create the Acheron dashboard FastAPI application.

    Reads the orchestrator URL from the ``ACHERON_URL`` environment variable
    when not provided explicitly.
    """
    if orchestrator_url is None:
        orchestrator_url = os.environ.get("ACHERON_URL", "http://localhost:8000")
    orchestrator_url = orchestrator_url.rstrip("/")
    app = FastAPI(title="Acheron Dashboard")

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
        data = await _fetch_orchestrator(orchestrator_url, _jobs_path(request))
        return _TEMPLATES.TemplateResponse(request, "partials/jobs.html", context={"jobs": data.get("jobs", [])})

    @app.get("/partials/jobs/{job_id}", response_class=HTMLResponse)
    async def job_detail_partial(request: Request, job_id: str) -> HTMLResponse:
        return await _job_detail_partial(orchestrator_url, request, job_id)

    @app.get("/partials/workers", response_class=HTMLResponse)
    async def workers_partial(request: Request) -> HTMLResponse:
        data = await _fetch_orchestrator(orchestrator_url, "/workers")
        return _TEMPLATES.TemplateResponse(
            request, "partials/workers.html", context={"workers": data.get("workers", [])}
        )

    @app.get("/partials/cost", response_class=HTMLResponse)
    async def cost_partial(request: Request) -> HTMLResponse:
        return await _cost_partial(orchestrator_url, request)

    @app.get("/partials/status", response_class=HTMLResponse)
    async def status_partial(request: Request) -> HTMLResponse:  # noqa: ARG001
        """Proxy the orchestrator's status partial; show Disconnected on failure."""
        return await _proxy_status_partial(orchestrator_url)

    return app
