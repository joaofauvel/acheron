"""Capability discovery route."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from fastapi import APIRouter, Request

from acheron.shell.api.schemas import CapabilitiesResponse, LanguagePair

if TYPE_CHECKING:
    from acheron.shell.orchestrator import Orchestrator

router = APIRouter()


def _get_orchestrator(request: Request) -> Orchestrator:
    return request.app.state.orchestrator  # type: ignore[no-any-return]


@router.get("/capabilities", response_model=CapabilitiesResponse)
async def get_capabilities(
    request: Request,
    src: str | None = None,
    dest: str | None = None,
) -> CapabilitiesResponse:
    """Aggregate language pair support from registered workers."""
    orch = _get_orchestrator(request)
    caps = orch.get_capabilities(src=src, dst=dest)
    raw_pairs = cast("list[dict[str, object]]", caps["language_pairs"])
    return CapabilitiesResponse(
        language_pairs=[
            LanguagePair(
                src=str(p["src"]),
                dst=str(p["dst"]),
                workers=cast("list[str]", p["workers"]),
            )
            for p in raw_pairs
        ]
    )
