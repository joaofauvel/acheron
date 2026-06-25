"""Cloud-side adapter + edge forwarder for RunPod workers.

``runpod.serverless.start({"handler": fn})`` expects ``fn(job: dict) -> dict``.
We wrap a :class:`WorkerHandler` so the same handler module runs inside
the RunPod serverless runtime image — its ``handle()`` contract is
identical whether the caller is the cloud-side handler loop or (in a
future sub-project) a local edge runtime.

The reverse direction — :class:`RunPodForwarderHandler` — is the
``WorkerHandler`` implementation that runs *inside* the edge container.
It accepts ``/execute`` from the orchestrator and forwards the job to a
RunPod serverless endpoint via :class:`RunPodClient`. The cloud-side
RunPod image (which has the GPU + model) does the actual inference and
returns artifacts.
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any, cast

from acheron.core.errors import WorkerError
from acheron.core.models import Job, WorkerCapabilities, WorkerType
from acheron.worker_sdk._runpod_client import RunPodClient, RunPodJobResult
from acheron.worker_sdk.artifacts import BytesArtifact
from acheron.worker_sdk.handler import WorkerHandler
from acheron.worker_sdk.inputs import BytesInput

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from acheron.worker_sdk.artifacts import Artifact
    from acheron.worker_sdk.inputs import Input
    from acheron.worker_sdk.settings import WorkerSettings


def make_runpod_handler(
    handler: WorkerHandler,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return a RunPod-compatible async callable wrapping ``handler``."""

    async def _rp_handler(runpod_job: dict[str, Any]) -> dict[str, Any]:
        job = _deserialise_job(runpod_job["input"])
        audio_payload = runpod_job["input"].get("input_audio")
        if audio_payload is not None:
            if not isinstance(audio_payload, dict):
                msg = f"RunPod input_audio must be a dict, got {type(audio_payload).__name__}"
                raise WorkerError(msg)
            data_b64 = audio_payload.get("data", "")
            if not isinstance(data_b64, str):
                msg = "RunPod input_audio.data must be a str (base64-encoded bytes)"
                raise WorkerError(msg)
            metadata_raw = audio_payload.get("metadata", {})
            if not isinstance(metadata_raw, dict):
                msg = "RunPod input_audio.metadata must be a dict"
                raise WorkerError(msg)
            content_type_raw = audio_payload.get("content_type", "audio/wav")
            if not isinstance(content_type_raw, str):
                msg = f"RunPod input_audio.content_type must be a str, got {type(content_type_raw).__name__}"
                raise WorkerError(msg)
            input_obj = BytesInput(
                content_type=content_type_raw,
                data=base64.b64decode(data_b64),
                metadata=dict(metadata_raw),
            )
            artifacts = await handler.handle(job, input_obj)
        else:
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


async def _serialise_job_for_runpod(job: Job, input: Input | None = None) -> dict[str, Any]:  # noqa: A002
    """Serialise a Job + optional Input into the RunPod /run input shape.

    The ``input_audio`` field is the base64-encoded body of an ``Input`` (8b);
    RunPod's /run wire is JSON, so binary inputs round-trip via base64.
    """
    out: dict[str, Any] = {
        "input": {
            "job_id": job.job_id,
            "job_type": job.job_type.value,
            "payload": dict(job.payload),
            "chapter_id": job.chapter_id,
            "sequence_ids": list(job.sequence_ids) if job.sequence_ids else [],
        }
    }
    if input is not None:
        body = b"".join([chunk async for chunk in input.stream()])
        out["input"]["input_audio"] = {
            "content_type": input.content_type,
            "data": base64.b64encode(body).decode("ascii"),
            "metadata": dict(input.metadata),
        }
    return out


def _deserialise_runpod_artifacts(result: RunPodJobResult) -> list[Artifact]:
    """Decode the RunPod response into transport-neutral Artifacts."""
    out: list[Artifact] = []
    for entry in result.artifacts:
        # RunPodJobResult.artifacts is typed list[dict[str, object]] — each
        # entry IS a dict by construction. The str() conversions below also
        # accept any object shape and raise a clean error if the API drifts.
        filename = entry.get("filename")
        content_type = entry.get("content_type")
        data_b64 = entry.get("data")
        if not isinstance(filename, str) or not isinstance(content_type, str) or not isinstance(data_b64, str):
            msg = "RunPod artifact missing required str fields (filename/content_type/data)"
            raise WorkerError(msg)
        metadata_raw = entry.get("metadata") or {}
        metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
        out.append(
            BytesArtifact(
                filename=filename,
                content_type=content_type,
                data=base64.b64decode(data_b64),
                metadata=dict(metadata),
            )
        )
    return out


class RunPodForwarderHandler(WorkerHandler):
    """Generic RunPod forwarder — runs in the GPU-less edge container.

    Accepts ``/execute`` from the orchestrator, serialises the Job into
    RunPod's ``/run`` input shape, awaits the cloud-side handler's
    response via :class:`RunPodClient`, and returns the resulting
    artifacts to the orchestrator as if the edge did the work itself.

    Capabilities are taken from a *phantom* handler class — the cloud-side
    handler module is bundled into the edge image (it imports lazily, so
    no GPU deps are needed at import time) and instantiated just to read
    its static ``capabilities()``. This keeps the capabilities metadata
    in a single source of truth (the worker module) without re-declaring
    it in YAML.

    The CLI loads both classes via the ``handler:`` and ``phantom_handler:``
    fields of the edge's ``worker.yaml``.
    """

    def __init__(
        self,
        settings: WorkerSettings,
        *,
        phantom_handler: type[WorkerHandler] | None = None,
    ) -> None:
        self._settings = settings
        if phantom_handler is not None:
            # The phantom's __init__ is not part of the WorkerHandler Protocol
            # (each handler defines its own constructor); we type-ignore the call
            # site rather than widening the Protocol signature.
            self._phantom: WorkerHandler | None = phantom_handler(settings)  # type: ignore[call-arg]
        else:
            self._phantom = None
        self._client: RunPodClient | None = None

    def capabilities(self) -> WorkerCapabilities:
        """Delegate to the phantom handler (or raise if none is configured)."""
        if self._phantom is not None:
            return self._phantom.capabilities()
        msg = (
            "RunPodForwarderHandler has no phantom_handler configured; "
            "set 'phantom_handler' in worker.yaml to the cloud-side handler class."
        )
        raise WorkerError(msg)

    async def startup(self) -> None:
        """Construct the RunPod client (single open, reused for each /execute)."""
        if self._settings.runpod_api_key is None or self._settings.runpod_endpoint_id is None:
            msg = (
                "runpod_api_key and runpod_endpoint_id are required for the RunPod forwarder. "
                "Set ACHERON_WORKER__RUNPOD_API_KEY and ACHERON_WORKER__RUNPOD_ENDPOINT_ID."
            )
            raise WorkerError(msg)
        self._client = RunPodClient(
            api_key=self._settings.runpod_api_key,
            endpoint_id=self._settings.runpod_endpoint_id,
            execution_timeout_s=self._settings.execution_timeout_s,
            base_url=self._settings.runpod_base_url,
        )

    async def shutdown(self) -> None:
        """Drop the client; RunPod SDK has no explicit close."""
        self._client = None

    async def handle(self, job: Job, input: Input | None = None) -> list[Artifact]:  # noqa: A002
        """Forward the job (and optional audio input) to RunPod and decode artifacts."""
        if self._client is None:
            msg = "RunPodClient not initialised (startup() not run)"
            raise WorkerError(msg)
        payload = await _serialise_job_for_runpod(job, input)
        result = await self._client.run(payload)
        return _deserialise_runpod_artifacts(result)


__all__ = [
    "RunPodForwarderHandler",
    "make_runpod_handler",
]
