"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from acheron.shell.api.routes import capabilities, jobs, workers
from acheron.shell.cache import PlanCache
from acheron.shell.orchestrator import Orchestrator
from acheron.shell.registry import WorkerRegistry


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    orch: Orchestrator = app.state.orchestrator
    await orch.start()
    yield
    await orch.shutdown()


def create_app(
    registry: WorkerRegistry | None = None,
    cache: PlanCache | None = None,
    data_dir: Path = Path("/data/jobs"),
) -> FastAPI:
    """Create and configure the FastAPI application."""
    if registry is None:
        registry = WorkerRegistry()
    if cache is None:
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

    return app
