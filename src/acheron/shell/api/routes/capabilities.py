"""Capability discovery route."""

from __future__ import annotations

from fastapi import APIRouter

from acheron.shell.api.deps import OrchestratorDep  # noqa: TC001
from acheron.shell.api.schemas import CapabilitiesResponse, LanguagePair

router = APIRouter()


@router.get("/capabilities", response_model=CapabilitiesResponse)
async def get_capabilities(
    orch: OrchestratorDep,
    src: str | None = None,
    dest: str | None = None,
) -> CapabilitiesResponse:
    """Aggregate language pair support from registered workers."""
    pairs = orch.get_capabilities(src=src, dst=dest)
    return CapabilitiesResponse(
        language_pairs=[LanguagePair(src=p.src, dst=p.dst, workers=list(p.workers)) for p in pairs]
    )
