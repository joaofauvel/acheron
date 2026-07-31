"""Worker record type used by the registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from acheron.core.models import WorkerErrorEvent, WorkerStatus

if TYPE_CHECKING:
    from acheron.core.models import JsonValue, WorkerCapabilities


_MAX_WORKER_ERROR_HISTORY = 10


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
    last_error: str | None = None
    status: WorkerStatus = WorkerStatus.HEALTHY
    booting_since: float | None = None
    registration_generation: int = 1
    error_history: tuple[WorkerErrorEvent, ...] = ()

    def __post_init__(self) -> None:
        if self.registration_generation < 1:
            msg = "registration_generation must be positive"
            raise ValueError(msg)
        if len(self.error_history) > _MAX_WORKER_ERROR_HISTORY:
            msg = "error_history must contain at most 10 entries"
            raise ValueError(msg)
