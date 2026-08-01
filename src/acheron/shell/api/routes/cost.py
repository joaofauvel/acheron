"""Cost explanation and aggregate estimate routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from acheron.core.errors import JobNotFoundError
from acheron.core.schemas import CostSummaryResponse, CostWindowQuery, JobCostResponse
from acheron.shell.api.deps import OrchestratorDep, RegistrationTokenDep  # noqa: TC001
from acheron.shell.api.routes.jobs import _error_response

router = APIRouter()


@router.get("/jobs/{job_id}/cost", response_model=JobCostResponse)
async def get_job_cost(job_id: str, orch: OrchestratorDep, _token: RegistrationTokenDep) -> JobCostResponse:
    """Return persisted execution-time cost evidence for a job."""
    cost = await orch.get_job_cost(job_id)
    if cost is None:
        error = JobNotFoundError("job not found")
        raise HTTPException(status_code=404, detail=_error_response(error).model_dump()) from error
    return cost


@router.get("/cost", response_model=CostSummaryResponse)
async def get_cost_summary(
    window: Annotated[CostWindowQuery, Query()],
    orch: OrchestratorDep,
    _token: RegistrationTokenDep,
) -> CostSummaryResponse:
    """Return an aggregate estimate for the requested query-string window."""
    return await orch.get_cost_summary(window.window)
