"""Worker record type used by the registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from acheron.core.models import WorkerCapabilities


@dataclass
class RegisteredWorker:
    """A worker tracked by the registry."""

    worker_id: str
    endpoint: str
    transport: str
    capabilities: WorkerCapabilities
    consecutive_failures: int = 0
    last_health_check: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
