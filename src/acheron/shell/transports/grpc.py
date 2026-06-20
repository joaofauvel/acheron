"""gRPC transport for remote TTS workers with server-side streaming."""

from __future__ import annotations

import logging
import time

import grpc
import grpc.aio
from grpc.health.v1 import health_pb2, health_pb2_grpc

from acheron.core.errors import WorkerError, WorkerUnavailableError
from acheron.core.interfaces import Worker
from acheron.core.models import (
    Job,
    JobMetrics,
    JobResult,
    JobStatus,
    OutputFile,
    WorkerCapabilities,
    WorkerType,
)
from acheron.proto import synthesis_pb2, synthesis_pb2_grpc

logger = logging.getLogger(__name__)


class GrpcWorker(Worker):
    """Worker that delegates TTS execution to a remote gRPC endpoint."""

    def __init__(self, channel: grpc.aio.Channel) -> None:
        self._channel = channel
        self._stub = synthesis_pb2_grpc.SynthesisStub(channel)  # type: ignore[no-untyped-call]
        self._health_stub = health_pb2_grpc.HealthStub(channel)

    async def capabilities(self) -> WorkerCapabilities:  # noqa: D102
        return WorkerCapabilities(
            worker_type=WorkerType.TTS,
            supported_languages_in=frozenset({"en", "es", "fr", "de"}),
            supported_languages_out=frozenset({"en", "es", "fr", "de"}),
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
            text=job.payload.get("text", ""),
            language=job.payload.get("language", ""),
            model=job.payload.get("model", ""),
        )

        pcm_chunks: list[bytes] = []
        start_time = time.monotonic()

        try:
            async for chunk in self._stub.Synthesize(request):
                pcm_chunks.append(chunk.pcm_data)  # noqa: PERF401
        except grpc.aio.AioRpcError as exc:
            if exc.code() == grpc.StatusCode.UNAVAILABLE:
                msg = f"Worker unavailable: {exc.details()}"
                raise WorkerUnavailableError(msg) from exc
            msg = f"gRPC error {exc.code()}: {exc.details()}"
            raise WorkerError(msg) from exc

        duration = time.monotonic() - start_time
        audio_data = b"".join(pcm_chunks)

        return JobResult(
            job_id=job.job_id,
            status=JobStatus.SUCCESS,
            outputs=(
                OutputFile(
                    path=f"{job.job_id}.pcm",
                    filename=f"{job.job_id}.pcm",
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
