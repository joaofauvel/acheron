"""Internal FastAPI app served by the edge container.

Routes: ``GET /health``, ``GET /capabilities``, ``POST /execute``.

``/execute`` emits a ``multipart/mixed`` body: one binary part per
:class:`Artifact` returned by the handler, plus a trailing
``application/json`` part carrying ``JobMetrics`` (duration, gpu_seconds,
cost_estimate, cost_basis). On handler failure the response is a plain
JSON ``ExecuteError`` body with status 500.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response

from acheron.core.models import Job, JobMetrics, WorkerCapabilities, WorkerType
from acheron.worker_sdk.artifacts import Artifact, BytesArtifact, FileArtifact, StreamArtifact
from acheron.worker_sdk.pricing import PriceSource, to_cost_basis
from acheron.worker_sdk.schemas import ExecuteError, ExecuteRequest

if TYPE_CHECKING:
    from acheron.worker_sdk.handler import WorkerHandler

logger = logging.getLogger(__name__)


def _caps_to_response(caps: WorkerCapabilities) -> dict[str, Any]:
    return {
        "worker_type": caps.worker_type.value,
        "supported_languages_in": sorted(caps.supported_languages_in),
        "supported_languages_out": sorted(caps.supported_languages_out),
        "supported_formats_in": sorted(caps.supported_formats_in),
        "supported_formats_out": sorted(caps.supported_formats_out),
        "max_payload_bytes": caps.max_payload_bytes,
        "batch_capable": caps.batch_capable,
        "model_source": caps.model_source,
        "metadata": caps.metadata,
    }


def _job_from_request(body: ExecuteRequest) -> Job:
    return Job(
        job_id=body.job_id,
        job_type=WorkerType(body.job_type),
        payload=dict(body.payload),
        chapter_id=body.chapter_id,
        sequence_ids=tuple(body.sequence_ids) if body.sequence_ids is not None else None,
    )


def _encode_metadata(metadata: dict[str, Any]) -> str:
    return json.dumps(metadata, separators=(",", ":"))


async def _build_multipart_response(
    artifacts: list[Artifact],
    metrics: JobMetrics,
) -> Response:
    """Serialize ``artifacts`` + ``metrics`` as a ``multipart/mixed`` body.

    One part per artifact with its own ``Content-Type`` + filename + metadata
    header, plus a trailing ``application/json`` part carrying ``metrics``.
    """
    boundary = f"acheron-{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for a in artifacts:
        header = (
            f"--{boundary}\r\n"
            f'Content-Disposition: attachment; filename="{a.filename}"\r\n'
            f"Content-Type: {a.content_type}\r\n"
            f"X-Acheron-Metadata: {_encode_metadata(a.metadata)}\r\n\r\n"
        ).encode("utf-8")
        body_data = b""
        async for chunk in a.stream():
            body_data += chunk
        parts.append(header + body_data + b"\r\n")
    metrics_json = (
        f'{{"duration_seconds":{metrics.duration_seconds}'
        f',"gpu_seconds":{metrics.gpu_seconds!r}'
        f',"tokens_in":{metrics.tokens_in!r}'
        f',"tokens_out":{metrics.tokens_out!r}'
        f',"cost_estimate":{metrics.cost_estimate!r}'
        f',"cost_basis":"{metrics.cost_basis.value if metrics.cost_basis else "unknown"}"'
        f"}}"
    ).encode("utf-8")
    parts.append(
        f"--{boundary}\r\nContent-Type: application/json\r\n\r\n".encode("utf-8")
        + metrics_json
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)
    return Response(
        content=body,
        media_type=f"multipart/mixed; boundary={boundary}",
    )


class EdgeApp:
    """Container for the edge FastAPI app + handler + price source."""

    def __init__(
        self,
        *,
        handler: "WorkerHandler",
        capabilities: WorkerCapabilities,
        price_source: PriceSource | None = None,
    ) -> None:
        self.handler = handler
        self.capabilities = capabilities
        self.price_source = price_source

        @asynccontextmanager
        async def lifespan(app: FastAPI) -> AsyncIterator[None]:  # noqa: ARG001
            await handler.startup()
            try:
                yield
            finally:
                await handler.shutdown()

        app = FastAPI(title="acheron-worker-edge", lifespan=lifespan)

        @app.get("/health")
        async def health() -> dict[str, str]:
            return {"status": "ok"}

        @app.get("/capabilities")
        async def get_capabilities() -> dict[str, Any]:
            return _caps_to_response(self.capabilities)

        @app.post("/execute")
        async def execute(body: ExecuteRequest) -> Response:
            return await self._run_execute(body)

        self.app = app

    async def _run_execute(self, body: ExecuteRequest) -> Response:
        job = _job_from_request(body)
        start = time.monotonic()
        try:
            artifacts: list[Artifact] = await self.handler.handle(job)
        except Exception as exc:
            logger.exception("Handler failed for job %s", job.job_id)
            return JSONResponse(
                status_code=500,
                content=ExecuteError(status="failed", error=str(exc)).model_dump(),
            )
        duration = time.monotonic() - start
        gpu_seconds = duration  # edge forwarder has no GPU; the handler times itself.
        if self.price_source is not None:
            est = await self.price_source.estimate(gpu_seconds)
            cost = est.cost
            basis = to_cost_basis(est)
        else:
            cost = None
            basis = None
        metrics = JobMetrics(
            duration_seconds=duration,
            gpu_seconds=gpu_seconds,
            cost_estimate=cost,
            cost_basis=basis,
        )
        return await _build_multipart_response(artifacts, metrics)


# Re-export variants for callers that want to type-narrow before passing to
# EdgeApp — they're the only Artifact subclasses today.
__all__ = [
    "BytesArtifact",
    "EdgeApp",
    "FileArtifact",
    "StreamArtifact",
]
