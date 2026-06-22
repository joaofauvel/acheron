"""Platform-specific health provider plugins for cold-start detection."""

from __future__ import annotations

from abc import ABC, abstractmethod

from acheron.core.models import WorkerStatus


class HealthProvider(ABC):
    """Query a hosting platform API to determine if a worker is booting or offline."""

    @abstractmethod
    async def check_status(self, endpoint_id: str) -> WorkerStatus:
        """Query the platform to verify if the container is booting vs offline."""
        ...
