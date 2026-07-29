"""Allowlisted job output downloads."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from acheron.core.schemas import ErrorResponse
from acheron.shell.api.deps import OrchestratorDep  # noqa: TC001

router = APIRouter()


def _not_found(error_type: str, message: str, remediation: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail=ErrorResponse(type=error_type, message=message, remediation=remediation).model_dump(),
    )


def safe_output_path(data_dir: Path, job_id: str, filename: str) -> Path:
    """Resolve a listed artifact below its canonical job directory."""
    if not filename or Path(filename).is_absolute() or Path(filename).name != filename:
        raise _not_found("OutputNotFoundError", f"Output not found: {filename}", f"acheron job status {job_id}")
    if not job_id or Path(job_id).is_absolute() or Path(job_id).name != job_id:
        raise _not_found("OutputNotFoundError", f"Output not found: {filename}", f"acheron job status {job_id}")

    data_root = data_dir.resolve()
    job_root = data_root / job_id
    try:
        resolved = (job_root / filename).resolve(strict=True)
        resolved.relative_to(job_root.resolve())
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise _not_found(
            "OutputNotFoundError", f"Output not found: {filename}", f"acheron job status {job_id}"
        ) from exc
    if not resolved.is_file():
        raise _not_found("OutputNotFoundError", f"Output not found: {filename}", f"acheron job status {job_id}")
    return resolved


@router.get("/{job_id}/outputs/{filename}")
async def get_job_output(job_id: str, filename: str, orch: OrchestratorDep) -> FileResponse:
    """Serve an output listed by the requested job."""
    tracked = await orch.get_job(job_id)
    if tracked is None:
        raise _not_found("JobNotFoundError", f"Job not found: {job_id}", "acheron jobs")
    if tracked.result is None:
        raise _not_found("OutputNotFoundError", f"Output not found: {filename}", f"acheron job status {job_id}")

    output = next((item for item in tracked.result.outputs if item.filename == filename), None)
    if output is None:
        raise _not_found("OutputNotFoundError", f"Output not found: {filename}", f"acheron job status {job_id}")
    path = safe_output_path(orch.settings.orchestrator.data_dir, job_id, filename)
    return FileResponse(path, media_type=output.content_type, filename=output.filename)
