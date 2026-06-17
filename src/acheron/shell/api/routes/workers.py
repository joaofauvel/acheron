"""Worker registration and listing routes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request

from acheron.core.models import WorkerCapabilities, WorkerType
from acheron.shell.api.schemas import (
    WorkerListResponse,
    WorkerRegistrationRequest,
    WorkerResponse,
)

if TYPE_CHECKING:
    from acheron.shell.orchestrator import Orchestrator

router = APIRouter()


def _get_orchestrator(request: Request) -> Orchestrator:
    return request.app.state.orchestrator  # type: ignore[no-any-return]


@router.post("", status_code=201, response_model=WorkerResponse)
async def register_worker(body: WorkerRegistrationRequest, request: Request) -> WorkerResponse:
    """Register a new worker."""
    orch = _get_orchestrator(request)

    try:
        worker_type = WorkerType(body.capabilities.worker_type)
    except ValueError as exc:
        msg = f"Invalid worker_type: {body.capabilities.worker_type}"
        raise HTTPException(status_code=400, detail=msg) from exc

    capabilities = WorkerCapabilities(
        worker_type=worker_type,
        supported_languages_in=frozenset(body.capabilities.supported_languages_in),
        supported_languages_out=frozenset(body.capabilities.supported_languages_out),
        supported_formats_in=frozenset(body.capabilities.supported_formats_in),
        supported_formats_out=frozenset(body.capabilities.supported_formats_out),
        max_payload_bytes=body.capabilities.max_payload_bytes,
        batch_capable=body.capabilities.batch_capable,
        model_source=body.capabilities.model_source,
    )

    orch.register_worker(body.worker_id, body.endpoint, body.transport, capabilities)

    return WorkerResponse(
        worker_id=body.worker_id,
        endpoint=body.endpoint,
        transport=body.transport,
        worker_type=body.capabilities.worker_type,
        consecutive_failures=0,
    )


@router.get("", response_model=WorkerListResponse)
async def list_workers(request: Request) -> WorkerListResponse:
    """List all registered workers."""
    orch = _get_orchestrator(request)
    workers = orch.list_workers()
    return WorkerListResponse(
        workers=[
            WorkerResponse(
                worker_id=w.worker_id,
                endpoint=w.endpoint,
                transport=w.transport,
                worker_type=w.capabilities.worker_type.value,
                consecutive_failures=w.consecutive_failures,
            )
            for w in workers
        ]
    )
