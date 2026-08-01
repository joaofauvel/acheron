"""Allowlisted job output downloads."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from acheron.core.schemas import ErrorResponse
from acheron.shell.api.deps import OrchestratorDep  # noqa: TC001
from acheron.shell.api.public import public_content_type, public_filename

if TYPE_CHECKING:
    from starlette.types import Receive, Scope, Send

router = APIRouter()


def _not_found(error_type: str, message: str, remediation: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail=ErrorResponse(type=error_type, message=message, remediation=remediation).model_dump(),
    )


def _is_path_component(value: str) -> bool:
    """Return whether a value is one non-traversing relative path component."""
    path = Path(value)
    return bool(value) and value not in {".", ".."} and not path.is_absolute() and path.name == value


def _relative_output_parts(data_root: Path, raw_job_root: Path, stored_path: str) -> tuple[str, ...]:
    stored = Path(stored_path)
    if not stored.is_absolute():
        stored_parts = stored.parts
        root_parts = data_root.parts
        for prefix_length in range(min(len(stored_parts), len(root_parts)), 0, -1):
            if stored_parts[:prefix_length] == root_parts[-prefix_length:]:
                stored = Path(*stored_parts[prefix_length:])
                break
        stored = data_root / stored
    stored = Path(os.path.normpath(stored))
    try:
        relative = stored.relative_to(raw_job_root)
    except ValueError as exc:
        raise ValueError(stored_path) from exc
    if not relative.parts or any(part in {".", ".."} for part in relative.parts):
        raise ValueError(stored_path)
    return relative.parts


def _require_regular_file(result: os.stat_result, stored_path: str) -> None:
    if not stat.S_ISREG(result.st_mode):
        raise ValueError(stored_path)


def _open_output_fd(data_dir: Path, job_id: str, stored_path: str) -> tuple[int, os.stat_result]:
    """Open a stored output without following path components or replacement races."""
    if not _is_path_component(job_id):
        raise _not_found("OutputNotFoundError", "Output not found", "acheron job status")

    data_root = data_dir.resolve()
    raw_job_root = data_root / job_id
    if raw_job_root.is_symlink():
        raise _not_found("OutputNotFoundError", "Output not found", "acheron job status")

    file_fd: int | None = None
    directory_fd: int | None = None
    try:
        relative_parts = _relative_output_parts(data_root, raw_job_root, stored_path)
        directory_fd = os.open(raw_job_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for component in relative_parts[:-1]:
            next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        file_fd = os.open(relative_parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        result = os.fstat(file_fd)
        _require_regular_file(result, stored_path)
    except (FileNotFoundError, OSError, ValueError) as exc:
        if file_fd is not None:
            os.close(file_fd)
        raise _not_found("OutputNotFoundError", "Output not found", "acheron job status") from exc
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
    return file_fd, result


class _PinnedFileResponse(FileResponse):
    """Serve a file through an already-open descriptor."""

    def __init__(
        self,
        file_fd: int,
        *,
        stat_result: os.stat_result,
        media_type: str | None = None,
        filename: str | None = None,
    ) -> None:
        self._file_fd = file_fd
        super().__init__(
            f"/proc/self/fd/{file_fd}",
            stat_result=stat_result,
            media_type=media_type,
            filename=filename,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        extensions = scope.get("extensions", {})
        if "http.response.pathsend" in extensions:
            scope = {
                **scope,
                "extensions": {key: value for key, value in extensions.items() if key != "http.response.pathsend"},
            }
        try:
            await super().__call__(scope, receive, send)
        finally:
            os.close(self._file_fd)


@router.get("/{job_id}/outputs/{output_index:int}")
async def get_job_output(job_id: str, output_index: int, orch: OrchestratorDep) -> FileResponse:
    """Serve an output listed by its persisted position."""
    tracked = await orch.get_job(job_id)
    if tracked is None:
        raise _not_found("JobNotFoundError", "Job not found", "acheron jobs")
    if tracked.result is None:
        raise _not_found("OutputNotFoundError", "Output not found", "acheron job status")

    if output_index < 0:
        raise _not_found("OutputNotFoundError", "Output not found", "acheron job status")
    try:
        output = tracked.result.outputs[output_index]
    except IndexError as exc:
        raise _not_found("OutputNotFoundError", "Output not found", "acheron job status") from exc
    file_fd, stat_result = _open_output_fd(orch.settings.orchestrator.data_dir, job_id, output.path)
    return _PinnedFileResponse(
        file_fd,
        stat_result=stat_result,
        media_type=public_content_type(output.content_type),
        filename=public_filename(output.filename),
    )
