"""Storage backends for the orchestrator."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, NoReturn

if TYPE_CHECKING:
    from acheron.shell.stores.base import JobStore, WorkerStore


def _unknown_backend(backend: str) -> NoReturn:
    msg = f"Unknown ACHERON_STORE_BACKEND: {backend}"
    raise ValueError(msg)


class _StoreSelection:
    backend: str
    redis_url: str

    def __init__(self, backend: str, redis_url: str) -> None:
        self.backend = backend
        self.redis_url = redis_url

    def worker_store(self) -> WorkerStore:
        from acheron.shell.stores.memory import InMemoryWorkerStore  # noqa: PLC0415

        match self.backend:
            case "memory":
                return InMemoryWorkerStore()
            case "redis":
                from acheron.shell.stores.redis import RedisWorkerStore  # noqa: PLC0415

                return RedisWorkerStore(self.redis_url)
            case _:
                _unknown_backend(self.backend)

    def job_store(self) -> JobStore:
        from acheron.shell.stores.memory import InMemoryJobStore  # noqa: PLC0415

        match self.backend:
            case "memory":
                return InMemoryJobStore()
            case "redis":
                from acheron.shell.stores.redis import RedisJobStore  # noqa: PLC0415

                return RedisJobStore(self.redis_url)
            case _:
                _unknown_backend(self.backend)


def _select_stores() -> _StoreSelection:
    """Read ``ACHERON_STORE_BACKEND`` and ``REDIS_URL`` once for the whole process."""
    backend = os.environ.get("ACHERON_STORE_BACKEND", "memory")
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    return _StoreSelection(backend, redis_url)


def create_worker_store() -> WorkerStore:
    """Create a worker store based on the ``ACHERON_STORE_BACKEND`` env var.

    Returns an in-memory store when ``ACHERON_STORE_BACKEND`` is unset or
    ``"memory"``, a Redis-backed store when ``"redis"``. Other values raise
    ``ValueError``. The Redis backend fails fast on unreachable Redis.
    """
    return _select_stores().worker_store()


def create_job_store() -> JobStore:
    """Create a job store based on the ``ACHERON_STORE_BACKEND`` env var."""
    return _select_stores().job_store()


def create_stores() -> tuple[WorkerStore, JobStore]:
    """Read ``ACHERON_STORE_BACKEND`` once and construct both stores from the same selection.

    The two stores are guaranteed to be on the same backend, eliminating the
    split-brain risk of two independent ``ACHERON_STORE_BACKEND`` reads.
    """
    selection = _select_stores()
    return selection.worker_store(), selection.job_store()
