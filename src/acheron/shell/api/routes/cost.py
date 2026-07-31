"""Cost explanation and aggregate estimate routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from acheron.core.errors import JobNotFoundError
from acheron.core.schemas import CostSummaryResponse, CostWindowQuery, JobCostResponse
from acheron.shell.api.deps import OrchestratorDep  # noqa: TC001

router = APIRouter()


@router.get("/jobs/{job_id}/cost", response_model=JobCostResponse)
async def get_job_cost(job_id: str, orch: OrchestratorDep) -> JobCostResponse:
    """Return persisted execution-time cost evidence for a job."""
    cost = await orch.get_job_cost(job_id)
    if cost is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}") from JobNotFoundError(job_id)
    return cost


@router.get("/cost", response_model=CostSummaryResponse)
async def get_cost_summary(
    window: Annotated[CostWindowQuery, Query()],
    orch: OrchestratorDep,
) -> CostSummaryResponse:
    """Return an aggregate estimate for the requested query-string window."""
    return await orch.get_cost_summary(window.window)
