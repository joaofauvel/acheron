"""Capability discovery route."""

from __future__ import annotations

from fastapi import APIRouter

from acheron.core.schemas import CapabilitiesResponse, LanguagePair
from acheron.shell.api.deps import OrchestratorDep  # noqa: TC001

router = APIRouter()


@router.get("/capabilities", response_model=CapabilitiesResponse)
async def get_capabilities(
    orch: OrchestratorDep,
    src: str | None = None,
    dest: str | None = None,
) -> CapabilitiesResponse:
    """Aggregate language pair support from registered workers."""
    pairs = await orch.get_capabilities(src=src, dst=dest)
    return CapabilitiesResponse(
        language_pairs=[LanguagePair(src=p.src, dst=p.dst, workers=list(p.workers)) for p in pairs]
    )
