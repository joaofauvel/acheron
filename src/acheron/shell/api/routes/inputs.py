"""Authenticated upload route for client files."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, UploadFile, status

from acheron.core.schemas import InputResponse
from acheron.shell.api.deps import OrchestratorDep, RegistrationTokenDep  # noqa: TC001
from acheron.shell.input_store import InputPathError, InputStore, InputTooLargeError, StoredInput

logger = logging.getLogger(__name__)

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
    stored: StoredInput | None = None
    try:
        store = InputStore(orch.settings.orchestrator.data_dir)
        try:
            stored = await store.save(file.filename or "", file.content_type, _chunks(file))
        finally:
            await file.close()
    except InputTooLargeError as exc:
        raise HTTPException(status_code=413, detail="input exceeds the 2 GiB upload limit") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid input filename") from exc
    except OSError as exc:
        logger.warning("Input storage failed: %s", exc)
        if stored is not None:
            try:
                InputStore(orch.settings.orchestrator.data_dir, create=False).delete(stored.input_id)
            except OSError as cleanup_exc:
                logger.warning("Failed to roll back input storage: %s", cleanup_exc)
            except InputPathError:
                logger.exception("Failed to roll back input storage")
        raise HTTPException(status_code=503, detail="input storage failed") from exc
    assert stored is not None  # noqa: S101
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
        await orch.delete_input(input_id)
    except InputPathError as exc:
        if str(exc) == "input is referenced by a job":
            raise HTTPException(status_code=409, detail="input is referenced by a job") from exc
        logger.warning("Rejected input deletion %r: %s", input_id, exc)
        raise HTTPException(status_code=422, detail="invalid input identity") from exc
    except OSError as exc:
        logger.warning("Input deletion failed for %s: %s", input_id, exc)
        raise HTTPException(status_code=503, detail="input deletion failed") from exc
