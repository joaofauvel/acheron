"""Capability discovery route."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, HTTPException, Query

from acheron.core.models import WorkerType
from acheron.core.schemas import CapabilitiesResponse, LanguagePair, WorkerCapability
from acheron.shell.api.deps import OrchestratorDep  # noqa: TC001

if TYPE_CHECKING:
    from acheron.shell.registry import RegisteredWorker


router = APIRouter()
_TYPED_WORKER_TYPES = frozenset({WorkerType.TTS, WorkerType.ASR, WorkerType.TRANSLATION})


def _supported_languages(workers: tuple[RegisteredWorker, ...]) -> list[str]:
    """Return all worker input and output languages in sorted order."""
    supported = {
        language
        for worker in workers
        for language in worker.capabilities.supported_languages_in | worker.capabilities.supported_languages_out
    }
    return sorted(supported)


@router.get("/capabilities", response_model=CapabilitiesResponse)
async def get_capabilities(
    orch: OrchestratorDep,
    src: str | None = None,
    dest: str | None = None,
    worker_type: Annotated[str | None, Query(alias="type")] = None,
) -> CapabilitiesResponse:
    """Return typed worker inventories or aggregated language pairs."""
    workers = await orch.list_workers()

    if worker_type is not None:
        if src is not None or dest is not None:
            msg = "type cannot be combined with src or dest"
            raise HTTPException(status_code=422, detail=msg)
        matching_type = next(
            (candidate for candidate in _TYPED_WORKER_TYPES if candidate.value == worker_type),
            None,
        )
        if matching_type is None:
            supported_types = ", ".join(sorted(candidate.value for candidate in _TYPED_WORKER_TYPES))
            msg = f"Invalid worker type '{worker_type}'; supported types: {supported_types}"
            raise HTTPException(status_code=422, detail=msg)
        return CapabilitiesResponse(
            language_pairs=[],
            workers=[
                WorkerCapability(
                    worker_id=worker.worker_id,
                    worker_type=worker.capabilities.worker_type.value,
                    model_source=worker.capabilities.model_source,
                    metadata=dict(worker.capabilities.metadata),
                )
                for worker in sorted(workers, key=lambda item: item.worker_id)
                if worker.capabilities.worker_type is matching_type
            ],
        )

    supported_languages = _supported_languages(workers)
    if src is not None and src not in supported_languages:
        supported = ", ".join(supported_languages)
        msg = f"source language '{src}' is not supported by any registered worker; supported sources: {supported}"
        raise HTTPException(status_code=422, detail=msg)
    if dest is not None and dest not in supported_languages:
        supported = ", ".join(supported_languages)
        msg = f"destination language '{dest}' is not supported by any registered worker; supported targets: {supported}"
        raise HTTPException(status_code=422, detail=msg)

    pairs = await orch.get_capabilities(src=src, dst=dest)
    return CapabilitiesResponse(
        language_pairs=[LanguagePair(src=p.src, dst=p.dst, workers=list(p.workers)) for p in pairs],
        workers=[],
    )
