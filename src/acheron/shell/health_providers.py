"""Platform-specific health provider plugins for cold-start detection."""

from __future__ import annotations

from abc import ABC, abstractmethod

import httpx

from acheron.core.models import WorkerStatus


class HealthProvider(ABC):
    """Query a hosting platform API to determine if a worker is booting or offline."""

    @abstractmethod
    async def check_status(self, endpoint_id: str) -> WorkerStatus:
        """Query the platform to verify if the container is booting vs offline."""
        ...


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
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self._BASE_URL}/endpoints/{endpoint_id}",
                    headers=headers,
                    timeout=10.0,
                )
        except (httpx.HTTPError, OSError):
            return WorkerStatus.OFFLINE
        if resp.status_code == 200:
            return WorkerStatus.BOOTING
        return WorkerStatus.OFFLINE


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
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self._BASE_URL}/{endpoint_id}",
                    headers=headers,
                    timeout=10.0,
                )
        except (httpx.HTTPError, OSError):
            return WorkerStatus.OFFLINE
        if resp.status_code != 200:
            return WorkerStatus.OFFLINE
        data = resp.json()
        status_raw = data.get("status")
        if isinstance(status_raw, dict):
            state = status_raw.get("state", "")
        elif isinstance(status_raw, str):
            state = status_raw
        else:
            state = ""
        if state in self._BOOTING_STATES:
            return WorkerStatus.BOOTING
        return WorkerStatus.OFFLINE
