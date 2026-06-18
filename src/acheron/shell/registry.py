"""In-memory worker registry with health tracking."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from acheron.core.models import WorkerCapabilities, WorkerType


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


_MAX_FAILURES = 3


class WorkerRegistry:
    """In-memory store mapping worker IDs to their endpoint and capabilities."""

    def __init__(self) -> None:
        self._workers: dict[str, RegisteredWorker] = {}

    def register(
        self,
        worker_id: str,
        endpoint: str,
        transport: str,
        capabilities: WorkerCapabilities,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Register a new worker or re-register an existing one."""
        self._workers[worker_id] = RegisteredWorker(
            worker_id=worker_id,
            endpoint=endpoint,
            transport=transport,
            capabilities=capabilities,
            consecutive_failures=0,
            last_health_check=time.time(),
            metadata=metadata or {},
        )

    def unregister(self, worker_id: str) -> None:
        """Remove a worker from the registry."""
        self._workers.pop(worker_id, None)

    def get(self, worker_id: str) -> RegisteredWorker | None:
        """Look up a worker by ID."""
        return self._workers.get(worker_id)

    def list_all(self) -> tuple[RegisteredWorker, ...]:
        """Return all registered workers."""
        return tuple(self._workers.values())

    def find_by_type(self, worker_type: WorkerType) -> tuple[RegisteredWorker, ...]:
        """Find workers matching a given WorkerType."""
        return tuple(w for w in self._workers.values() if w.capabilities.worker_type == worker_type)

    def find_by_language(self, src: str, dst: str) -> tuple[RegisteredWorker, ...]:
        """Find workers supporting a source→target language pair."""
        return tuple(
            w
            for w in self._workers.values()
            if src in w.capabilities.supported_languages_in and dst in w.capabilities.supported_languages_out
        )

    def record_health_failure(self, worker_id: str) -> bool:
        """Record a health check failure. Returns True if worker was removed."""
        worker = self._workers.get(worker_id)
        if worker is None:
            return False
        worker.consecutive_failures += 1
        worker.last_health_check = time.time()
        if worker.consecutive_failures >= _MAX_FAILURES:
            self.unregister(worker_id)
            return True
        return False

    def record_health_success(self, worker_id: str) -> None:
        """Record a successful health check, resetting failure counter."""
        worker = self._workers.get(worker_id)
        if worker is not None:
            worker.consecutive_failures = 0
            worker.last_health_check = time.time()
