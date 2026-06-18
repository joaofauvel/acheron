"""Storage backends for the orchestrator."""

from __future__ import annotations

import os
from typing import NoReturn

from acheron.shell.stores.base import JobStore, WorkerStore


def _unknown_backend(backend: str) -> NoReturn:
    msg = f"Unknown ACHERON_STORE_BACKEND: {backend}"
    raise ValueError(msg)


def create_worker_store() -> WorkerStore:
    """Create a worker store based on the ``ACHERON_STORE_BACKEND`` env var.

    Returns an in-memory store when ``ACHERON_STORE_BACKEND`` is unset or
    ``"memory"``, a Redis-backed store when ``"redis"``. Other values raise
    ``ValueError``. The Redis backend fails fast on unreachable Redis.
    """
    from acheron.shell.stores.memory import InMemoryWorkerStore  # noqa: PLC0415

    backend = os.environ.get("ACHERON_STORE_BACKEND", "memory")
    match backend:
        case "memory":
            return InMemoryWorkerStore()
        case "redis":
            from acheron.shell.stores.redis import RedisWorkerStore  # noqa: PLC0415

            redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
            return RedisWorkerStore(redis_url)
        case _:
            _unknown_backend(backend)


def create_job_store() -> JobStore:
    """Create a job store based on the ``ACHERON_STORE_BACKEND`` env var."""
    from acheron.shell.stores.memory import InMemoryJobStore  # noqa: PLC0415

    backend = os.environ.get("ACHERON_STORE_BACKEND", "memory")
    match backend:
        case "memory":
            return InMemoryJobStore()
        case "redis":
            from acheron.shell.stores.redis import RedisJobStore  # noqa: PLC0415

            redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
            return RedisJobStore(redis_url)
        case _:
            _unknown_backend(backend)
