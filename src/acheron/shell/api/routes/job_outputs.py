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


def safe_output_path(data_dir: Path, job_id: str, stored_path: str) -> Path:
    """Resolve a persisted artifact beneath its canonical job directory."""
    if not _is_path_component(job_id):
        raise _not_found("OutputNotFoundError", "Output not found", f"acheron job status {job_id}")

    data_root = data_dir.resolve()
    try:
        job_root = (data_root / job_id).resolve(strict=True)
        job_root.relative_to(data_root)
        resolved = Path(stored_path).resolve(strict=True)
        resolved.relative_to(job_root)
        _require_file(resolved, stored_path)
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise _not_found("OutputNotFoundError", "Output not found", f"acheron job status {job_id}") from exc
    return resolved


def _require_file(path: Path, stored_path: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(stored_path)


def _is_path_component(value: str) -> bool:
    """Return whether a value is one non-traversing relative path component."""
    path = Path(value)
    return bool(value) and value not in {".", ".."} and not path.is_absolute() and path.name == value


@router.get("/{job_id}/outputs/{output_index:int}")
async def get_job_output(job_id: str, output_index: int, orch: OrchestratorDep) -> FileResponse:
    """Serve an output listed by its persisted position."""
    tracked = await orch.get_job(job_id)
    if tracked is None:
        raise _not_found("JobNotFoundError", f"Job not found: {job_id}", "acheron jobs")
    if tracked.result is None:
        raise _not_found("OutputNotFoundError", f"Output not found: {output_index}", f"acheron job status {job_id}")

    if output_index < 0:
        raise _not_found("OutputNotFoundError", f"Output not found: {output_index}", f"acheron job status {job_id}")
    try:
        output = tracked.result.outputs[output_index]
    except IndexError as exc:
        raise _not_found(
            "OutputNotFoundError", f"Output not found: {output_index}", f"acheron job status {job_id}"
        ) from exc
    path = safe_output_path(orch.settings.orchestrator.data_dir, job_id, output.path)
    return FileResponse(path, media_type=output.content_type, filename=output.filename)
