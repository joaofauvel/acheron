"""Self-registration client for the edge container."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import httpx

from acheron.worker_sdk._caps import caps_to_dict

if TYPE_CHECKING:
    from acheron.core.models import WorkerCapabilities

logger = logging.getLogger(__name__)

_MAX_BACKOFF_S = 30.0


async def register_with_orchestrator(  # noqa: PLR0913
    *,
    client: httpx.AsyncClient,
    orchestrator_url: str,
    token: str | None,
    worker_id: str,
    endpoint: str,
    transport: str,
    capabilities: WorkerCapabilities,
    retries: int = 30,
    retry_delay: float = 2.0,
) -> None:
    """Register the worker with exponential backoff on failure.

    Sleeps ``retry_delay * 2**attempt`` (capped at ``_MAX_BACKOFF_S``) between
    attempts; raises :class:`httpx.ConnectError` after ``retries`` consecutive
    failures so the edge container fails fast instead of looping forever.

    The wide kwargs surface is intentional — this is the public SDK entry
    point; callers compose each field from a ``WorkerSettings`` instance.
    """
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = {
        "worker_id": worker_id,
        "endpoint": endpoint,
        "transport": transport,
        "capabilities": caps_to_dict(capabilities),
    }

    url = f"{orchestrator_url.rstrip('/')}/workers"
    attempt = 0
    while True:
        try:
            resp = await client.post(url, json=payload, headers=headers, timeout=10.0)
            resp.raise_for_status()
        except (httpx.HTTPError, OSError) as exc:
            attempt += 1
            if attempt >= retries:
                msg = f"Could not register worker {worker_id} after {retries} attempts"
                raise httpx.ConnectError(msg) from exc
            backoff = min(retry_delay * 2 ** (attempt - 1), _MAX_BACKOFF_S)
            logger.debug("Orchestrator not ready (%s); retrying in %.1fs...", exc, backoff)
            await asyncio.sleep(backoff)
        else:
            logger.info("Registered %s with orchestrator", worker_id)
            return
