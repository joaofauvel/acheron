"""FastAPI application factory."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI

from acheron.shell.api.routes import capabilities, jobs, workers
from acheron.shell.cache import PlanCache
from acheron.shell.orchestrator import Orchestrator
from acheron.shell.stores import create_worker_store

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from acheron.shell.stores.base import WorkerStore


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage orchestrator lifecycle — start on startup, stop on shutdown."""
    orch: Orchestrator = app.state.orchestrator
    await orch.start()
    try:
        yield
    finally:
        await orch.shutdown()
        await orch.close()


def create_app(
    registry: WorkerStore | None = None,
    cache: PlanCache | None = None,
    data_dir: Path | str | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    ``ACHERON_DATA_DIR`` env var is consulted when ``data_dir`` is not provided.
    """
    if registry is None:
        registry = create_worker_store()
    if cache is None:
        if data_dir is None:
            data_dir = Path(os.environ.get("ACHERON_DATA_DIR", "/data/jobs"))
        cache = PlanCache(data_dir)

    orchestrator = Orchestrator(
        registry=registry,
        cache=cache,
    )

    app = FastAPI(
        title="Acheron",
        description="Distributed audio-transformation pipeline",
        lifespan=lifespan,
    )
    app.state.orchestrator = orchestrator

    app.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
    app.include_router(workers.router, prefix="/workers", tags=["workers"])
    app.include_router(capabilities.router, tags=["capabilities"])

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
