"""HTML partial endpoints served by the orchestrator for HTMX dashboard polling."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from acheron.core.models import WorkerStatus, WorkerType
from acheron.shell.api.deps import OrchestratorDep  # noqa: TC001

if TYPE_CHECKING:
    from acheron.shell.registry import RegisteredWorker

router = APIRouter()

_SERVICE_WORKER_TYPES = frozenset({WorkerType.ASR, WorkerType.TRANSLATION, WorkerType.TTS})


@dataclass(frozen=True)
class _ReadinessSummary:
    healthy_tts: int
    total_tts: int
    all_service_workers_healthy: bool

    @property
    def is_ready(self) -> bool:
        return self.healthy_tts > 0 and self.all_service_workers_healthy


def _summarize_readiness(workers: tuple[RegisteredWorker, ...]) -> _ReadinessSummary:
    tts_workers = tuple(worker for worker in workers if worker.capabilities.worker_type is WorkerType.TTS)
    service_workers = tuple(worker for worker in workers if worker.capabilities.worker_type in _SERVICE_WORKER_TYPES)
    return _ReadinessSummary(
        healthy_tts=sum(worker.status is WorkerStatus.HEALTHY for worker in tts_workers),
        total_tts=len(tts_workers),
        all_service_workers_healthy=all(worker.status is WorkerStatus.HEALTHY for worker in service_workers),
    )


def _render_readiness(summary: _ReadinessSummary) -> str:
    dot_class, label = ("dot-green", "Ready") if summary.is_ready else ("dot-yellow", "Waiting")
    return f'<span class="dot {dot_class}"></span> {label} ({summary.healthy_tts}/{summary.total_tts} TTS healthy)'


@router.get("/partials/status", response_class=HTMLResponse)
async def status_partial(orch: OrchestratorDep) -> HTMLResponse:
    """Return the current service-worker readiness badge."""
    workers = await orch.list_workers()
    return HTMLResponse(_render_readiness(_summarize_readiness(workers)))
