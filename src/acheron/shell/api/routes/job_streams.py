"""Streaming route handlers for job progress."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import HTTPException, Query
from starlette.responses import StreamingResponse

from acheron.core.errors import JobNotFoundError
from acheron.core.models import PlanStatus
from acheron.core.schemas import JobLogEvent, JobProgress
from acheron.shell.api.deps import OrchestratorDep, RegistrationTokenDep  # noqa: TC001
from acheron.shell.api.public import public_optional_worker_id
from acheron.shell.api.routes.job_responses import error_response

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


async def job_logs(
    job_id: str,
    orch: OrchestratorDep,
    _token: RegistrationTokenDep,
    *,
    follow: Annotated[bool, Query()] = True,
) -> StreamingResponse:
    """Stream job progress events as newline-delimited JSON."""
    from acheron.shell.job_events import iter_events  # noqa: PLC0415

    tracked = await orch.get_job(job_id)
    if tracked is None:
        raise HTTPException(
            status_code=404,
            detail=error_response(JobNotFoundError("Job not found")).model_dump(),
        )

    terminal = {PlanStatus.COMPLETED, PlanStatus.FAILED, PlanStatus.PARTIAL}
    if tracked.status in terminal or not follow:
        snapshot = JobLogEvent(
            job_id=tracked.job_id,
            timestamp=tracked.last_persisted_at or tracked.created_at,
            status=tracked.status,
            step_id=tracked.progress.current_step_id,
            worker_type=tracked.progress.current_worker_type,
            worker_id=public_optional_worker_id(tracked.progress.current_worker_id),
            progress=JobProgress(
                completed_steps=tracked.progress.completed_steps,
                total_steps=tracked.progress.total_steps,
                current_step_id=tracked.progress.current_step_id,
                current_worker_type=tracked.progress.current_worker_type,
                current_worker_id=public_optional_worker_id(tracked.progress.current_worker_id),
                eta_seconds=tracked.progress.eta_seconds,
            ),
            message=f"job {tracked.status.value}",
        )
        return StreamingResponse(
            iter([snapshot.model_dump_json().encode() + b"\n"]),
            media_type="application/x-ndjson",
        )

    events = orch.events
    queue = await events.subscribe(job_id)

    async def stream() -> AsyncIterator[bytes]:
        try:
            async for event in iter_events(queue):
                yield event.model_dump_json().encode() + b"\n"
        finally:
            await events.unsubscribe(job_id, queue)

    return StreamingResponse(stream(), media_type="application/x-ndjson")
