"""Worker registration and listing routes."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException

from acheron.core.models import WorkerCapabilities, WorkerStatus, WorkerType, sanitize_worker_error
from acheron.core.schemas import (
    WorkerErrorEventResponse,
    WorkerListResponse,
    WorkerRegistrationResponse,
    WorkerResponse,
)
from acheron.shell.api.deps import OrchestratorDep, RegistrationTokenDep  # noqa: TC001
from acheron.shell.api.schemas import WorkerRegistrationRequest  # noqa: TC001

if TYPE_CHECKING:
    from acheron.shell.registry import RegisteredWorker


router = APIRouter()
_BOOTING_TIMEOUT_SECONDS = 600.0


def _booting_elapsed_seconds(worker: RegisteredWorker, now: float) -> float | None:
    """Return elapsed BOOTING time, or ``None`` for other worker states."""
    if worker.status is not WorkerStatus.BOOTING or worker.booting_since is None:
        return None
    return max(0.0, now - worker.booting_since)


def _public_worker_response(worker: RegisteredWorker, now: float) -> WorkerResponse:
    return WorkerResponse(
        worker_id=worker.worker_id,
        endpoint=None,
        transport=worker.transport,
        worker_type=worker.capabilities.worker_type.value,
        consecutive_failures=worker.consecutive_failures,
        status=worker.status,
        last_error=sanitize_worker_error(worker.last_error) if worker.last_error else None,
        error_history=[
            WorkerErrorEventResponse(
                timestamp=event.timestamp,
                message=sanitize_worker_error(event.message),
                consecutive_failures=event.consecutive_failures,
            )
            for event in worker.error_history[-10:]
        ],
        max_input_tokens=worker.capabilities.max_input_tokens,
        booting_elapsed_seconds=_booting_elapsed_seconds(worker, now),
        booting_timeout_seconds=_BOOTING_TIMEOUT_SECONDS,
    )


@router.post("", status_code=201, response_model=WorkerRegistrationResponse)
async def register_worker(
    body: WorkerRegistrationRequest,
    orch: OrchestratorDep,
    _token: RegistrationTokenDep,
) -> WorkerRegistrationResponse:
    """Register a new worker."""
    try:
        worker_type = WorkerType(body.capabilities.worker_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid worker type") from exc

    capabilities = WorkerCapabilities(
        worker_type=worker_type,
        supported_languages_in=frozenset(body.capabilities.supported_languages_in),
        supported_languages_out=frozenset(body.capabilities.supported_languages_out),
        supported_formats_in=frozenset(body.capabilities.supported_formats_in),
        supported_formats_out=frozenset(body.capabilities.supported_formats_out),
        max_payload_bytes=body.capabilities.max_payload_bytes,
        batch_capable=body.capabilities.batch_capable,
        model_source=body.capabilities.model_source,
        max_input_tokens=body.capabilities.max_input_tokens,
        metadata=body.capabilities.metadata,
    )

    await orch.register_worker(body.worker_id, body.endpoint, body.transport, capabilities)
    worker = await orch._registry.get(body.worker_id)  # noqa: SLF001
    if worker is None:
        raise HTTPException(status_code=500, detail="worker registration did not persist")
    return WorkerRegistrationResponse(worker_id=worker.worker_id, status=worker.status)


@router.get("", response_model=WorkerListResponse)
async def list_workers(orch: OrchestratorDep) -> WorkerListResponse:
    """List sanitized worker lifecycle state without registration auth."""
    workers = await orch.list_workers()
    now = time.time()
    return WorkerListResponse(workers=[_public_worker_response(worker, now) for worker in workers])
