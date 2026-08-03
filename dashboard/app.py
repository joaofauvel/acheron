"""HTMX dashboard for the Acheron orchestrator."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from urllib.parse import quote, urlencode, urlsplit

import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from starlette.requests import Request  # noqa: TC002

from acheron.core.schemas import CostSummaryResponse
from dashboard.booting_progress import clamp_booting_elapsed, format_booting_elapsed

_LOGGER = logging.getLogger(__name__)
_SECONDS_PER_MINUTE = 60
_MINUTES_PER_HOUR = 60
_HOURS_PER_DAY = 24
_VERSION_MAX_LENGTH = 64
_SHA_MAX_LENGTH = 64
_REQUEST_ID_MAX_LENGTH = 128
_CONTROL_CHARACTER_LIMIT = 32
_DELETE_CHARACTER = 127
_MAX_URL_PORT = 65535
_VERSION_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._+-!")
_SHA_CHARS = frozenset("0123456789abcdefABCDEF")
_REQUEST_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-")
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


def _registration_token() -> str | None:
    """Read the explicit token or the mounted token file for one request."""
    configured = os.environ.get("ACHERON_REGISTRATION_TOKEN", "").strip()
    if configured:
        return configured
    token_file = os.environ.get("ACHERON_REGISTRATION_TOKEN_FILE", "").strip()
    if not token_file:
        return None
    try:
        token = Path(token_file).read_text(encoding="utf-8").strip()
    except OSError, UnicodeError:
        _LOGGER.warning("Dashboard registration token file is unavailable")
        return None
    return token or None


async def _fetch_orchestrator(orchestrator_url: str, path: str) -> dict[str, object]:
    """GET ``path`` from the orchestrator; return ``{}`` on any fetch failure."""
    try:
        token = _registration_token()
        headers = {"Authorization": f"Bearer {token}"} if token else None
        async with httpx.AsyncClient(base_url=orchestrator_url) as client:
            resp = await client.get(path, headers=headers)
            resp.raise_for_status()
            payload = resp.json()
            match payload:
                case dict() as data:
                    return cast("dict[str, object]", data)
                case _:
                    return {}
    except Exception:  # noqa: BLE001
        _LOGGER.warning("Dashboard cannot reach orchestrator at %s%s", orchestrator_url, path)
        return {}


def _metadata_string(
    value: object,
    fallback: str,
    *,
    max_length: int,
    allowed_chars: frozenset[str],
    min_length: int = 1,
) -> str:
    """Return a bounded, validated string for operator-facing metadata."""
    match value:
        case str() as text if text.strip():
            text = text.strip()
            if min_length <= len(text) <= max_length and all(char in allowed_chars for char in text):
                return text
        case _:
            pass
    return fallback


async def _fetch_version(orchestrator_url: str) -> dict[str, str | None]:
    """Fetch the public version identity without exposing other response fields."""
    try:
        async with httpx.AsyncClient(base_url=orchestrator_url) as client:
            resp = await client.get("/version")
            resp.raise_for_status()
            payload = resp.json()
            match payload:
                case dict() as metadata:
                    pass
                case _:
                    return {"version": "unknown", "sha": "unknown", "request_id": None}
            version = _metadata_string(
                metadata.get("version"),
                "unknown",
                max_length=_VERSION_MAX_LENGTH,
                allowed_chars=_VERSION_CHARS,
            )
            sha = _metadata_string(
                metadata.get("sha"),
                "unknown",
                max_length=_SHA_MAX_LENGTH,
                min_length=7,
                allowed_chars=_SHA_CHARS,
            )
            request_id = (
                _metadata_string(
                    resp.headers.get("x-request-id"),
                    "",
                    max_length=_REQUEST_ID_MAX_LENGTH,
                    allowed_chars=_REQUEST_ID_CHARS,
                )
                or None
            )
            return {"version": version, "sha": sha[:7], "request_id": request_id}
    except Exception:  # noqa: BLE001
        _LOGGER.warning("Dashboard cannot reach orchestrator at %s/version", orchestrator_url)
        return {"version": "unknown", "sha": "unknown", "request_id": None}


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


def _normalise_base_url(value: str, *, setting_name: str) -> str:
    """Validate and normalize an absolute HTTP(S) base URL."""
    normalized = value.strip()
    try:
        parsed = urlsplit(normalized)
        port = parsed.port
    except ValueError:
        parsed = None
        port = None
    hostname = parsed.hostname if parsed is not None else None
    if (
        parsed is None
        or parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or hostname is None
        or "\\" in parsed.netloc
        or "%" in parsed.netloc
        or parsed.netloc.endswith(":")
        or "?" in normalized
        or "#" in normalized
        or (port is not None and not 1 <= port <= _MAX_URL_PORT)
        or any(
            char.isspace() or ord(char) < _CONTROL_CHARACTER_LIMIT or ord(char) == _DELETE_CHARACTER
            for char in normalized
        )
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        msg = f"{setting_name} must be an absolute HTTP(S) URL without credentials, query, or fragment"
        raise ValueError(msg)
    return normalized.rstrip("/")


async def _job_detail_partial(
    orchestrator_url: str,
    browser_url: str,
    request: Request,
    job_id: str,
) -> HTMLResponse:
    """Render a job detail fragment from the orchestrator response."""
    data = await _fetch_orchestrator(orchestrator_url, f"/jobs/{quote(job_id, safe='')}")
    return _TEMPLATES.TemplateResponse(
        request,
        "partials/job_detail.html",
        context={"job": data, "orchestrator_url": browser_url},
    )


async def _cost_partial(orchestrator_url: str, request: Request) -> HTMLResponse:
    """Render cost windows and the bounded job snapshot from one response."""
    window = request.query_params.get("window", "7d")
    if window not in {"24h", "7d", "30d", "all"}:
        window = "7d"
    raw_summary = await _fetch_orchestrator(orchestrator_url, f"/cost?window={window}")
    try:
        summary = CostSummaryResponse.model_validate(raw_summary).model_dump(mode="json")
    except ValidationError:
        summary = {}
    raw_jobs = summary.get("jobs", [])
    jobs = cast("list[dict[str, object]]", raw_jobs) if isinstance(raw_jobs, list) else []
    return _TEMPLATES.TemplateResponse(
        request,
        "partials/cost.html",
        context={"jobs": jobs, "summary": summary, "window": window},
    )


def create_app(orchestrator_url: str | None = None, browser_url: str | None = None) -> FastAPI:
    """Create the Acheron dashboard FastAPI application.

    Reads the internal orchestrator URL from ``ACHERON_URL`` and the
    browser-facing URL from ``ACHERON_BROWSER_URL`` when not provided explicitly.
    The browser-facing URL defaults to the internal URL for local deployments.
    """
    if orchestrator_url is None:
        orchestrator_url = os.environ.get("ACHERON_URL", "http://localhost:8000")
    orchestrator_url = _normalise_base_url(orchestrator_url, setting_name="orchestrator_url")
    if browser_url is None:
        browser_url = os.environ.get("ACHERON_BROWSER_URL", orchestrator_url)
    browser_url = _normalise_base_url(browser_url, setting_name="browser_url")
    app = FastAPI(title="Acheron Dashboard")

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        # Only trust X-Forwarded-User when behind a reverse proxy that
        # authenticates and strips the header. Unauthenticated clients can
        # set this header to any value; no access decision depends on it.
        user = ""
        if os.environ.get("ACHERON_TRUST_REVERSE_PROXY") == "1":
            user = request.headers.get("X-Forwarded-User", "")
        version = await _fetch_version(orchestrator_url)
        return _TEMPLATES.TemplateResponse(request, "index.html", context={"user": user, "version": version})

    @app.get("/partials/jobs", response_class=HTMLResponse)
    async def jobs_partial(request: Request) -> HTMLResponse:
        data = await _fetch_orchestrator(orchestrator_url, _jobs_path(request))
        return _TEMPLATES.TemplateResponse(request, "partials/jobs.html", context={"jobs": data.get("jobs", [])})

    @app.get("/partials/jobs/{job_id}", response_class=HTMLResponse)
    async def job_detail_partial(request: Request, job_id: str) -> HTMLResponse:
        return await _job_detail_partial(orchestrator_url, browser_url, request, job_id)

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
