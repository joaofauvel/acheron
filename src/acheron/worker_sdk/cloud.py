"""Cloud-side adapter wrapping a WorkerHandler as a RunPod-compatible callable.

``runpod.serverless.start({"handler": fn})`` expects ``fn(job: dict) -> dict``.
We wrap a :class:`WorkerHandler` so the same handler module runs inside
the RunPod serverless runtime image — its ``handle()`` contract is
identical whether the caller is the cloud-side handler loop or (in a
future sub-project) a local edge runtime.
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any, cast

from acheron.core.models import Job, WorkerType

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from acheron.worker_sdk.artifacts import Artifact
    from acheron.worker_sdk.handler import WorkerHandler


def make_runpod_handler(
    handler: WorkerHandler,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return a RunPod-compatible async callable wrapping ``handler``."""

    async def _rp_handler(runpod_job: dict[str, Any]) -> dict[str, Any]:
        job = _deserialise_job(runpod_job["input"])
        artifacts = await handler.handle(job)
        return {"artifacts": [await _serialise(a) for a in artifacts]}

    return _rp_handler


def _deserialise_job(input_payload: dict[str, Any]) -> Job:
    return Job(
        job_id=input_payload["job_id"],
        job_type=WorkerType(input_payload["job_type"]),
        payload=cast("dict[str, Any]", input_payload.get("payload", {})),
        chapter_id=input_payload.get("chapter_id", ""),
        sequence_ids=(tuple(input_payload["sequence_ids"]) if input_payload.get("sequence_ids") else None),
    )


async def _serialise(artifact: Artifact) -> dict[str, Any]:
    body = b"".join([chunk async for chunk in artifact.stream()])
    return {
        "filename": artifact.filename,
        "content_type": artifact.content_type,
        "data": base64.b64encode(body).decode("ascii"),
        "metadata": artifact.metadata,
    }
