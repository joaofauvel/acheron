"""Capability discovery route."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, HTTPException, Query

from acheron.core.models import WorkerStatus, WorkerType
from acheron.core.schemas import CapabilitiesResponse, LanguagePair, WorkerCapability
from acheron.shell.api.deps import OrchestratorDep  # noqa: TC001
from acheron.shell.api.public import public_capability_values, public_worker_id

if TYPE_CHECKING:
    from acheron.shell.capabilities import LanguagePair as AggregatedLanguagePair
    from acheron.shell.registry import RegisteredWorker


router = APIRouter()
_TYPED_WORKER_TYPES = frozenset({WorkerType.TTS, WorkerType.ASR, WorkerType.TRANSLATION})
_SPEAKER_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 .'-]{0,63}$")
_SPEAKER_FORBIDDEN_RE = re.compile(
    r"(?:https?|grpc|redis|token|secret|password|credential|api[_ -]?key|provider|request|response|body|bearer)",
    re.IGNORECASE,
)


def _public_speakers(worker: RegisteredWorker) -> list[str]:
    if worker.capabilities.worker_type is not WorkerType.TTS:
        return []
    value = worker.capabilities.metadata.get("speakers")
    if not isinstance(value, list):
        return []
    speakers = {
        speaker.strip()
        for speaker in value
        if isinstance(speaker, str)
        and speaker.strip()
        and _SPEAKER_NAME_RE.fullmatch(speaker.strip())
        and not _SPEAKER_FORBIDDEN_RE.search(speaker.strip())
    }
    return sorted(speakers)[:100]


def _public_language_pair(pair: AggregatedLanguagePair) -> LanguagePair | None:
    """Project an aggregated pair without reflecting untrusted labels."""
    src_values = public_capability_values([pair.src], kind="language")
    dst_values = public_capability_values([pair.dst], kind="language")
    if not src_values or not dst_values:
        return None
    return LanguagePair(
        src=src_values[0],
        dst=dst_values[0],
        workers=[public_worker_id(worker_id) for worker_id in pair.workers],
    )


def _supported_languages(workers: tuple[RegisteredWorker, ...]) -> list[str]:
    """Return all worker input and output languages in sorted order."""
    supported = {
        language
        for worker in workers
        for language in public_capability_values(
            worker.capabilities.supported_languages_in | worker.capabilities.supported_languages_out,
            kind="language",
        )
    }
    return sorted(supported)


@router.get("/capabilities", response_model=CapabilitiesResponse)
async def get_capabilities(
    orch: OrchestratorDep,
    src: str | None = None,
    dest: str | None = None,
    worker_type: Annotated[str | None, Query(alias="type")] = None,
) -> CapabilitiesResponse:
    """Return typed worker inventories or aggregated language pairs."""
    all_workers = tuple(await orch.list_workers())

    if worker_type is not None:
        if src is not None or dest is not None:
            msg = "type cannot be combined with src or dest"
            raise HTTPException(status_code=422, detail=msg)
        matching_type = next(
            (candidate for candidate in _TYPED_WORKER_TYPES if candidate.value == worker_type),
            None,
        )
        if matching_type is None:
            supported_types = ", ".join(sorted(candidate.value for candidate in _TYPED_WORKER_TYPES))
            msg = f"Invalid worker type; supported types: {supported_types}"
            raise HTTPException(status_code=422, detail=msg)
        return CapabilitiesResponse(
            language_pairs=[],
            workers=[
                WorkerCapability(
                    worker_id=public_worker_id(worker.worker_id),
                    worker_type=worker.capabilities.worker_type.value,
                    supported_languages_in=public_capability_values(
                        worker.capabilities.supported_languages_in, kind="language"
                    ),
                    supported_languages_out=public_capability_values(
                        worker.capabilities.supported_languages_out, kind="language"
                    ),
                    supported_formats_in=public_capability_values(
                        worker.capabilities.supported_formats_in, kind="format"
                    ),
                    supported_formats_out=public_capability_values(
                        worker.capabilities.supported_formats_out, kind="format"
                    ),
                    max_payload_bytes=worker.capabilities.max_payload_bytes,
                    max_input_tokens=worker.capabilities.max_input_tokens,
                    batch_capable=worker.capabilities.batch_capable,
                    speakers=_public_speakers(worker),
                )
                for worker in sorted(all_workers, key=lambda item: (item.worker_id.casefold(), item.worker_id))
                if worker.status is WorkerStatus.HEALTHY
                and worker.capabilities.worker_type is matching_type
            ],
        )

    workers = tuple(worker for worker in all_workers if worker.status is WorkerStatus.HEALTHY)
    supported_languages = _supported_languages(workers)
    if src is not None and src not in supported_languages:
        supported = ", ".join(supported_languages)
        msg = f"source language is not supported by any registered worker; supported sources: {supported}"
        raise HTTPException(status_code=422, detail=msg)
    if dest is not None and dest not in supported_languages:
        supported = ", ".join(supported_languages)
        msg = f"destination language is not supported by any registered worker; supported targets: {supported}"
        raise HTTPException(status_code=422, detail=msg)

    pairs = await orch.get_capabilities(src=src, dst=dest)
    return CapabilitiesResponse(
        language_pairs=[public_pair for p in pairs if (public_pair := _public_language_pair(p)) is not None],
        workers=[],
    )
