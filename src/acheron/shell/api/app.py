"""FastAPI application factory."""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from acheron.core.errors import sanitise_public_message, sanitise_public_remediation
from acheron.shell.api.admin_audit import record_admin_failure
from acheron.shell.api.input_boundary import InputRequestBoundary
from acheron.shell.api.routes import (
    admin,
    capabilities,
    cost,
    inputs,
    job_outputs,
    jobs,
    partials,
    plans,
    version,
    workers,
)
from acheron.shell.api.schemas import AdminErrorResponse
from acheron.shell.cache import PlanCache
from acheron.shell.config import Settings, load_settings
from acheron.shell.logging_context import ContextFilter, bind_request_id
from acheron.shell.orchestrator import Orchestrator
from acheron.shell.stores import create_job_store, create_worker_store
from acheron.version import build_version

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from starlette.middleware.base import RequestResponseEndpoint
    from starlette.responses import Response

    from acheron.shell.stores.base import JobStore, WorkerStore

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage orchestrator lifecycle — start on startup, stop on shutdown."""
    orch: Orchestrator = app.state.orchestrator
    await orch.start()
    try:
        yield
    finally:
        try:
            await orch.shutdown()
        finally:
            await orch.close()


def create_app(  # noqa: C901, PLR0915
    registry: WorkerStore | None = None,
    job_store: JobStore | None = None,
    cache: PlanCache | None = None,
    data_dir: Path | str | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    ``ACHERON_DATA_DIR`` env var is consulted when ``data_dir`` is not provided.
    """
    version_info = build_version()
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
    app.state.version = version_info
    app.add_middleware(InputRequestBoundary)
    logging.getLogger().addFilter(ContextFilter())

    @app.middleware("http")
    async def _request_id_middleware(request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id
        with bind_request_id(request_id):
            try:
                response = await call_next(request)
            except Exception:
                if not request.url.path.startswith("/admin/"):
                    raise
                record_admin_failure(request, orchestrator, reason="unexpected administrative route failure")
                error = AdminErrorResponse(
                    type="AdminInternalError",
                    message="Administrative request failed",
                    remediation="Inspect the service logs and retry the operation.",
                )
                response = JSONResponse(status_code=500, content={"detail": error.model_dump()})
            response.headers["x-request-id"] = request_id
            return response

    @app.exception_handler(RequestValidationError)
    async def _admin_validation_error(request: Request, _exc: RequestValidationError) -> Response:
        if not request.url.path.startswith("/admin/"):
            return JSONResponse(status_code=422, content={"detail": "Request validation failed"})
        record_admin_failure(request, orchestrator, reason="request validation failed")
        error = AdminErrorResponse(
            type="AdminRequestValidationError",
            message="Administrative request validation failed",
            remediation="Submit the canonical JSON body with no unknown fields.",
        )
        return JSONResponse(status_code=422, content={"detail": error.model_dump()})

    @app.exception_handler(HTTPException)
    async def _admin_http_error(request: Request, exc: HTTPException) -> Response:
        raw_detail: object = exc.detail
        if not request.url.path.startswith("/admin/"):
            if isinstance(raw_detail, dict):
                detail = dict(raw_detail)
                for key in ("message", "remediation"):
                    value = detail.get(key)
                    if isinstance(value, str):
                        detail[key] = (
                            sanitise_public_remediation(value)
                            if key == "remediation"
                            else sanitise_public_message(value)
                        )
            else:
                detail = sanitise_public_message(str(raw_detail))
            return JSONResponse(status_code=exc.status_code, content={"detail": detail}, headers=exc.headers)
        match raw_detail:
            case dict() as raw_mapping:
                detail = dict(raw_mapping)
                for key in ("message", "remediation"):
                    value = detail.get(key)
                    if isinstance(value, str):
                        detail[key] = (
                            sanitise_public_remediation(value)
                            if key == "remediation"
                            else sanitise_public_message(value)
                        )
            case _:
                detail = {
                    "type": "AdminRequestError",
                    "message": sanitise_public_message(str(raw_detail)),
                    "remediation": None,
                }
        error = AdminErrorResponse.model_validate(detail)
        record_admin_failure(request, orchestrator, reason=error.message)
        return JSONResponse(status_code=exc.status_code, content={"detail": error.model_dump()}, headers=exc.headers)

    @app.exception_handler(Exception)
    async def _admin_unexpected_error(request: Request, exc: Exception) -> Response:
        if not request.url.path.startswith("/admin/"):
            raise exc
        record_admin_failure(request, orchestrator, reason="unexpected administrative route failure")
        error = AdminErrorResponse(
            type="AdminInternalError",
            message="Administrative request failed",
            remediation="Inspect the service logs and retry the operation.",
        )
        return JSONResponse(status_code=500, content={"detail": error.model_dump()})

    app.include_router(admin.router, prefix="/admin", tags=["admin"])
    app.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
    app.include_router(job_outputs.router, prefix="/jobs", tags=["job-outputs"])
    app.include_router(workers.router, prefix="/workers", tags=["workers"])
    app.include_router(inputs.router, prefix="/inputs", tags=["inputs"])
    app.include_router(capabilities.router, tags=["capabilities"])
    app.include_router(partials.router, tags=["partials"])
    app.include_router(plans.router, prefix="/plans", tags=["plans"])
    app.include_router(cost.router, tags=["cost"])
    app.include_router(version.router, tags=["version"])

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
