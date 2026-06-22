"""HTML partial endpoints served by the orchestrator for HTMX dashboard polling."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get("/partials/status", response_class=HTMLResponse)
async def status_partial() -> HTMLResponse:
    """Return a green 'Connected' badge.

    Reachability is the signal: if the dashboard can fetch this, the
    orchestrator is up. The dashboard renders a red 'Disconnected' badge
    when this endpoint is unreachable.
    """
    return HTMLResponse('<span class="dot dot-green"></span> Connected')
