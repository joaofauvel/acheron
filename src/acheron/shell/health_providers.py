"""Platform-specific health provider plugins for cold-start detection."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

from acheron.core.interfaces import HealthProvider
from acheron.core.models import WorkerStatus

if TYPE_CHECKING:
    from acheron.shell.config import Settings

logger = logging.getLogger(__name__)


async def _fetch_provider_response(
    provider_name: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,  # noqa: ASYNC109 - delegated to httpx
) -> httpx.Response | None:
    """Issue a GET to ``url`` and return the response, or ``None`` on transport failure.

    Centralises the AsyncClient lifecycle, timeout, and
    ``(httpx.HTTPError, OSError)``-to-``None`` translation that every
    provider shares. A ``None`` return signals "transport failed" and
    each provider maps that to ``WorkerStatus.OFFLINE``.
    """
    try:
        async with httpx.AsyncClient() as client:
            return await client.get(url, headers=headers or {}, timeout=timeout)
    except (httpx.HTTPError, OSError) as exc:
        logger.warning(
            "%s health check for %s failed: %s: %s",
            provider_name,
            url,
            type(exc).__name__,
            exc,
        )
        return None
class RunPodHealthProvider(HealthProvider):
    """RunPod Serverless health provider.

    ``endpoint_id`` is the RunPod serverless endpoint id. If the endpoint
    exists, the worker is treated as cold-starting (HTTP probe failed but the
    platform still knows about it). If the endpoint is gone, the worker is
    offline.
    """

    _BASE_URL = "https://rest.runpod.io/v1"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def check_status(self, endpoint_id: str) -> WorkerStatus:
        """Map endpoint existence to BOOTING (cold start) or OFFLINE."""
        resp = await _fetch_provider_response(
            type(self).__name__,
            f"{self._BASE_URL}/endpoints/{endpoint_id}",
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        if resp is None or resp.status_code != httpx.codes.OK:
            return WorkerStatus.OFFLINE
        return WorkerStatus.BOOTING


class HuggingFaceHealthProvider(HealthProvider):
    """Hugging Face Inference Endpoints health provider.

    ``endpoint_id`` is ``namespace/name``. The HF API returns a ``status.state``
    field. Initializing/starting/running states (when the HTTP probe failed)
    indicate a cold start. Paused/failed or missing endpoints are offline.
    """

    _BASE_URL = "https://api.endpoints.huggingface.cloud/v2/endpoints"
    _BOOTING_STATES = frozenset({"pending", "initializing", "starting", "running"})

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def check_status(self, endpoint_id: str) -> WorkerStatus:
        """Map ``status.state`` to BOOTING (initializing/starting/running) or OFFLINE."""
        resp = await _fetch_provider_response(
            type(self).__name__,
            f"{self._BASE_URL}/{endpoint_id}",
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        if resp is None or resp.status_code != httpx.codes.OK:
            return WorkerStatus.OFFLINE
        status_raw = resp.json().get("status")
        if isinstance(status_raw, dict):
            state = status_raw.get("state", "")
        elif isinstance(status_raw, str):
            state = status_raw
        else:
            state = ""
        if state in self._BOOTING_STATES:
            return WorkerStatus.BOOTING
        return WorkerStatus.OFFLINE


def create_health_providers(settings: Settings) -> dict[str, HealthProvider]:
    """Build the per-platform provider map from API keys in settings."""
    providers: dict[str, HealthProvider] = {}
    if settings.providers.runpod.api_key:
        providers["runpod"] = RunPodHealthProvider(settings.providers.runpod.api_key)
    if settings.providers.huggingface.api_key:
        providers["huggingface"] = HuggingFaceHealthProvider(settings.providers.huggingface.api_key)
    return providers
