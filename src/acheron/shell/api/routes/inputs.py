"""Authenticated upload route for client files."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, UploadFile, status

from acheron.core.schemas import InputResponse
from acheron.shell.api.deps import OrchestratorDep, RegistrationTokenDep  # noqa: TC001
from acheron.shell.input_store import InputStore, InputTooLargeError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

router = APIRouter()

_CHUNK_BYTES: int = 1 << 20  # 1 MiB


async def _chunks(file: UploadFile) -> AsyncIterator[bytes]:
    """Yield ``file`` in 1 MiB chunks until EOF."""
    while True:
        chunk = await file.read(_CHUNK_BYTES)
        if not chunk:
            return
        yield chunk


@router.post("", response_model=InputResponse, status_code=201)
async def upload_input(
    file: UploadFile,
    orch: OrchestratorDep,
    _token: RegistrationTokenDep,
) -> InputResponse:
    """Stream ``file`` to the orchestrator's input store and return the server-relative source path."""
    store = InputStore(orch.settings.orchestrator.data_dir)
    try:
        try:
            stored = await store.save(file.filename or "", file.content_type, _chunks(file))
        finally:
            await file.close()
    except InputTooLargeError as exc:
        raise HTTPException(status_code=413, detail="input exceeds the 2 GiB upload limit") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return InputResponse(
        input_id=stored.input_id,
        source_path=stored.source_path,
        filename=stored.filename,
        size_bytes=stored.size_bytes,
        content_type=stored.content_type,
    )


@router.delete("/{input_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_input(
    input_id: str,
    orch: OrchestratorDep,
    _token: RegistrationTokenDep,
) -> None:
    """Delete a temporary uploaded input; the operation is idempotent."""
    try:
        InputStore(orch.settings.orchestrator.data_dir).delete(input_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
