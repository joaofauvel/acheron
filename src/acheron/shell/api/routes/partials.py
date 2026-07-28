"""HTML partial endpoints served by the orchestrator for HTMX dashboard polling."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from acheron.core.models import WorkerStatus, WorkerType
from acheron.shell.api.deps import OrchestratorDep  # noqa: TC001

if TYPE_CHECKING:
    from acheron.shell.registry import RegisteredWorker

router = APIRouter()

_SERVICE_WORKER_TYPES = frozenset({WorkerType.ASR, WorkerType.TRANSLATION, WorkerType.TTS})


def _render_fleet_status(workers: tuple[RegisteredWorker, ...]) -> str:
    service_workers = tuple(worker for worker in workers if worker.capabilities.worker_type in _SERVICE_WORKER_TYPES)
    if not service_workers:
        return '<span class="dot dot-yellow"></span> Waiting for workers (0/0 service workers healthy)'

    grouped_workers: defaultdict[WorkerType, list[RegisteredWorker]] = defaultdict(list)
    for worker in service_workers:
        grouped_workers[worker.capabilities.worker_type].append(worker)

    details = ", ".join(
        f"{worker_type.value} "
        f"{sum(worker.status is WorkerStatus.HEALTHY for worker in grouped_workers[worker_type])}/"
        f"{len(grouped_workers[worker_type])}"
        for worker_type in sorted(grouped_workers, key=lambda worker_type: worker_type.value)
    )
    healthy_count = sum(worker.status is WorkerStatus.HEALTHY for worker in service_workers)
    dot_class, label = ("dot-green", "Ready") if healthy_count == len(service_workers) else ("dot-yellow", "Waiting")
    return f'<span class="dot {dot_class}"></span> {label} ({details})'


@router.get("/partials/status", response_class=HTMLResponse)
async def status_partial(orch: OrchestratorDep) -> HTMLResponse:
    """Return the current service-worker readiness badge."""
    workers = await orch.list_workers()
    return HTMLResponse(_render_fleet_status(workers))
