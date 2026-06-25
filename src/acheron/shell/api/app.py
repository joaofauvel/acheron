"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI

from acheron.shell.api.routes import capabilities, jobs, partials, workers
from acheron.shell.cache import PlanCache
from acheron.shell.config import Settings, load_settings
from acheron.shell.orchestrator import Orchestrator
from acheron.shell.stores import create_job_store, create_worker_store

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from acheron.shell.stores.base import JobStore, WorkerStore


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
    job_store: JobStore | None = None,
    cache: PlanCache | None = None,
    data_dir: Path | str | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    ``ACHERON_DATA_DIR`` env var is consulted when ``data_dir`` is not provided.
    """
    base_settings = settings if settings is not None else load_settings()
    if data_dir is not None:
        effective_data_dir = Path(data_dir)
        settings = base_settings.model_copy(
            update={"orchestrator": base_settings.orchestrator.model_copy(update={"data_dir": effective_data_dir})}
        )
    else:
        settings = base_settings
    if registry is None:
        registry = create_worker_store()
    if job_store is None:
        job_store = create_job_store()
    if cache is None:
        cache = PlanCache(settings.orchestrator.data_dir)

    orchestrator = Orchestrator(
        registry=registry,
        cache=cache,
        job_store=job_store,
        settings=settings,
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
    app.include_router(partials.router, tags=["partials"])

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
