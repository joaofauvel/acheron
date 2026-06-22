"""Worker registration and listing routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from acheron.core.models import WorkerCapabilities, WorkerStatus, WorkerType
from acheron.shell.api.deps import OrchestratorDep, RegistrationTokenDep  # noqa: TC001
from acheron.shell.api.schemas import (
    WorkerListResponse,
    WorkerRegistrationRequest,
    WorkerResponse,
)

router = APIRouter()


@router.post("", status_code=201, response_model=WorkerResponse)
async def register_worker(
    body: WorkerRegistrationRequest,
    orch: OrchestratorDep,
    _token: RegistrationTokenDep,
) -> WorkerResponse:
    """Register a new worker."""
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
        metadata=body.capabilities.metadata,
    )

    await orch.register_worker(body.worker_id, body.endpoint, body.transport, capabilities)

    return WorkerResponse(
        worker_id=body.worker_id,
        endpoint=body.endpoint,
        transport=body.transport,
        worker_type=body.capabilities.worker_type,
        consecutive_failures=0,
        status=WorkerStatus.HEALTHY.value,
        last_error=None,
    )


@router.get("", response_model=WorkerListResponse)
async def list_workers(orch: OrchestratorDep) -> WorkerListResponse:
    """List all registered workers."""
    workers = await orch.list_workers()
    return WorkerListResponse(
        workers=[
            WorkerResponse(
                worker_id=w.worker_id,
                endpoint=w.endpoint,
                transport=w.transport,
                worker_type=w.capabilities.worker_type.value,
                consecutive_failures=w.consecutive_failures,
                status=w.status.value,
                last_error=w.last_error,
            )
            for w in workers
        ]
    )
