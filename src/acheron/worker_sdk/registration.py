"""Self-registration client for the edge container.

Posts ``WorkerRegistrationRequest`` to the orchestrator's ``POST /workers``
route, with exponential backoff until the orchestrator is reachable. Tags
the worker's capabilities metadata with ``health_provider`` and
``health_endpoint_id`` for the existing RunPodHealthProvider cold-start
detection plumbing (Layer 11).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from acheron.core.models import WorkerCapabilities

logger = logging.getLogger(__name__)


async def register_with_orchestrator(
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
    """Register the worker, retrying until the orchestrator is reachable."""
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = {
        "worker_id": worker_id,
        "endpoint": endpoint,
        "transport": transport,
        "capabilities": _caps_to_dict(capabilities),
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
            logger.debug("Orchestrator not ready (%s); retrying...", exc)
            await asyncio.sleep(retry_delay)
        else:
            logger.info("Registered %s with orchestrator", worker_id)
            return


def _caps_to_dict(caps: WorkerCapabilities) -> dict[str, object]:
    """Serialize WorkerCapabilities for POST /workers."""
    metadata = dict(caps.metadata)
    return {
        "worker_type": caps.worker_type.value,
        "supported_languages_in": sorted(caps.supported_languages_in),
        "supported_languages_out": sorted(caps.supported_languages_out),
        "supported_formats_in": sorted(caps.supported_formats_in),
        "supported_formats_out": sorted(caps.supported_formats_out),
        "max_payload_bytes": caps.max_payload_bytes,
        "batch_capable": caps.batch_capable,
        "model_source": caps.model_source,
        "metadata": metadata,
    }
