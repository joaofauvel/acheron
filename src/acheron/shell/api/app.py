"""FastAPI application factory."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI

from acheron.shell.api.routes import capabilities, jobs, workers
from acheron.shell.cache import PlanCache
from acheron.shell.orchestrator import Orchestrator
from acheron.shell.registry import WorkerRegistry

if TYPE_CHECKING:
    from acheron.core.models import JobResult, Plan, PlanStep


async def _noop_handler(_step: PlanStep, _plan: Plan) -> JobResult:
    msg = "No step handler configured — submit a real handler via Orchestrator"
    raise NotImplementedError(msg)


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
        handler=_noop_handler,
    )

    app = FastAPI(title="Acheron", description="Distributed audio-transformation pipeline")
    app.state.orchestrator = orchestrator

    app.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
    app.include_router(workers.router, prefix="/workers", tags=["workers"])
    app.include_router(capabilities.router, tags=["capabilities"])

    return app
