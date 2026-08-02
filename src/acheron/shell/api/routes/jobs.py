"""Job route assembly."""

from __future__ import annotations

from fastapi import APIRouter

from acheron.core.schemas import JobListResponse, JobResponse, PlanResponse
from acheron.shell.api.routes.job_lifecycle import cancel_job, get_job, list_jobs, resume_job
from acheron.shell.api.routes.job_requests import preview_job, retry_job, submit_job
from acheron.shell.api.routes.job_streams import job_logs

router = APIRouter()

router.add_api_route("", submit_job, methods=["POST"], status_code=201, response_model=JobResponse)
router.add_api_route("/{job_id}/retry", retry_job, methods=["POST"], response_model=JobResponse)
router.add_api_route(":preview", preview_job, methods=["POST"], response_model=PlanResponse)
router.add_api_route("/{job_id}", get_job, methods=["GET"], response_model=JobResponse)
router.add_api_route("/{job_id}/cancel", cancel_job, methods=["POST"], response_model=JobResponse)
router.add_api_route("/{job_id}/logs", job_logs, methods=["GET"])
router.add_api_route("/{job_id}/resume", resume_job, methods=["POST"], response_model=JobResponse)
router.add_api_route("", list_jobs, methods=["GET"], response_model=JobListResponse)
