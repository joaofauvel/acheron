"""Persisted-plan lookup routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from acheron.core.errors import CacheCorruptedError, CacheMissError
from acheron.core.schemas import PlanResponse
from acheron.shell.api.deps import OrchestratorDep, RegistrationTokenDep  # noqa: TC001

router = APIRouter()


@router.get("/{plan_id}", response_model=PlanResponse)
async def get_plan(plan_id: str, orch: OrchestratorDep, _token: RegistrationTokenDep) -> PlanResponse:
    """Return the public structure of a previously-compiled plan.

    A ``CacheMissError`` (missing or unsafe plan id) becomes HTTP 404 so
    operators cannot tell apart "never compiled", "purged", and "invalid
    id" — and so the cache's on-disk layout is not exposed. A
    ``CacheCorruptedError`` becomes a generic HTTP 500 with no on-disk
    detail leaked into the response body.
    """
    try:
        plan = await orch.get_plan(plan_id)
    except CacheMissError as exc:
        raise HTTPException(status_code=404, detail="Plan not found") from exc
    except CacheCorruptedError as exc:
        raise HTTPException(status_code=500, detail="Cached plan could not be loaded") from exc
    return PlanResponse.from_plan(plan)
