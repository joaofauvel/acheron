"""Worker record type used by the registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from acheron.core.models import JsonValue, WorkerCapabilities


@dataclass
class RegisteredWorker:
    """A worker tracked by the registry.

    ``metadata`` holds JSON-serializable values only. In-process callables
    (e.g. local worker handlers) must NOT be stored here; use a side dict on
    the orchestrator instead.
    """

    worker_id: str
    endpoint: str
    transport: str
    capabilities: WorkerCapabilities
    consecutive_failures: int = 0
    last_health_check: float | None = None
    metadata: dict[str, JsonValue] = field(default_factory=dict)
