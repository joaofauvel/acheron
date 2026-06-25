"""gRPC transport for remote TTS workers — Artifact mode + legacy PCM streaming."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import grpc
import grpc.aio
from grpc.health.v1 import health_pb2, health_pb2_grpc

from acheron.core.errors import WorkerError, WorkerUnavailableError
from acheron.core.interfaces import Worker
from acheron.core.models import (
    SUPPORTED_LANGUAGES,
    Job,
    JobMetrics,
    JobResult,
    JobStatus,
    OutputFile,
    WorkerCapabilities,
    WorkerType,
)
from acheron.proto import synthesis_pb2, synthesis_pb2_grpc
from acheron.shell.transports._multipart import _build_result, _materialize_artifact

logger = logging.getLogger(__name__)


class GrpcWorker(Worker):
    """Worker that delegates TTS execution to a remote gRPC endpoint.

    ``OutputChunk`` carries an ``oneof payload``: ``pcm_data`` (legacy live
    streaming) or ``artifact`` (structured output, since Layer 8a). The
    orchestrator consumes ``Artifact`` parts via the shared
    ``_materialize_artifact`` / ``_build_result`` helpers — identical to the
    HTTP multipart path. Legacy ``pcm_data`` mode is preserved.

    ``data_dir`` is required — the orchestrator (which owns settings) passes
    it explicitly so the same transport works against the configured data
    dir without reading env vars directly.
    """

    def __init__(
        self,
        channel: grpc.aio.Channel,
        *,
        data_dir: Path | str,
    ) -> None:
        self._channel = channel
        self._stub = synthesis_pb2_grpc.SynthesisStub(channel)  # type: ignore[no-untyped-call]
        self._health_stub = health_pb2_grpc.HealthStub(channel)
        self._data_dir = Path(data_dir)

    async def capabilities(self) -> WorkerCapabilities:  # noqa: D102
        return WorkerCapabilities(
            worker_type=WorkerType.TTS,
            supported_languages_in=SUPPORTED_LANGUAGES,
            supported_languages_out=SUPPORTED_LANGUAGES,
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"wav", "pcm"}),
            max_payload_bytes=None,
            batch_capable=True,
            model_source=None,
        )

    async def execute(self, job: Job) -> JobResult:  # noqa: D102
        if job.job_type != WorkerType.TTS:
            msg = f"GrpcWorker only supports TTS, got {job.job_type}"
            raise WorkerError(msg)

        request = synthesis_pb2.SynthesisRequest(  # type: ignore[attr-defined]
            job_id=job.job_id,
            text=str(job.payload.get("text", "")),
            language=str(job.payload.get("language", "")),
            model=str(job.payload.get("model", "")),
        )

        start_time = time.monotonic()
        artifact_parts: list[synthesis_pb2.Artifact] = []  # type: ignore[name-defined]
        pcm_chunks: list[bytes] = []

        try:
            async for chunk in self._stub.Synthesize(request):
                payload_type = chunk.WhichOneof("payload")
                if payload_type == "artifact":
                    artifact_parts.append(chunk.artifact)
                elif payload_type == "pcm_data":
                    pcm_chunks.append(chunk.pcm_data)
        except grpc.aio.AioRpcError as exc:
            if exc.code() == grpc.StatusCode.UNAVAILABLE:
                msg = f"Worker unavailable: {exc.details()}"
                raise WorkerUnavailableError(msg) from exc
            msg = f"gRPC error {exc.code()}: {exc.details()}"
            raise WorkerError(msg) from exc

        duration = time.monotonic() - start_time

        if artifact_parts:
            return await self._assemble_artifacts(job.job_id, artifact_parts, duration)
        # Legacy PCM fallback — keep the prior behavior intact.
        return self._assemble_pcm(job.job_id, pcm_chunks, duration)

    async def _assemble_artifacts(
        self,
        job_id: str,
        artifacts: list[synthesis_pb2.Artifact],  # type: ignore[name-defined]
        duration: float,
    ) -> JobResult:
        plan_job_id = "-".join(job_id.split("-")[:-1]) if "-" in job_id else job_id
        step_id = job_id.rsplit("-", maxsplit=1)[-1] if "-" in job_id else "execute"
        dest_dir = self._data_dir / plan_job_id / step_id

        outputs: list[OutputFile] = []
        for art in artifacts:
            out = await _materialize_artifact(
                data=art.data,
                filename=art.filename,
                content_type=art.content_type,
                dest_dir=dest_dir,
            )
            outputs.append(out)
        # Plan 2 doesn't surface trailing-metadata metrics yet; the HTTP path
        # carries cost_basis. The gRPC path fills a basic metrics envelope; a
        # future sub-project wires trailing-metadata → JobMetrics.
        metrics = JobMetrics(duration_seconds=duration)
        return _build_result(job_id=job_id, outputs=tuple(outputs), metrics=metrics)

    def _assemble_pcm(self, job_id: str, pcm_chunks: list[bytes], duration: float) -> JobResult:
        audio_data = b"".join(pcm_chunks)
        return JobResult(
            job_id=job_id,
            status=JobStatus.SUCCESS,
            outputs=(
                OutputFile(
                    path=f"{job_id}.pcm",
                    filename=f"{job_id}.pcm",
                    size_bytes=len(audio_data),
                    checksum="",
                    content_type="audio/pcm",
                ),
            ),
            metrics=JobMetrics(duration_seconds=duration),
        )

    async def health(self) -> bool:  # noqa: D102
        try:
            response = await self._health_stub.Check(health_pb2.HealthCheckRequest())
        except grpc.aio.AioRpcError:
            return False
        else:
            return response.status == health_pb2.HealthCheckResponse.SERVING  # type: ignore[no-any-return]
