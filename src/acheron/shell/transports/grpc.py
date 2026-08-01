"""gRPC transport for remote TTS workers — Artifact mode + legacy PCM streaming."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

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
    OutputFile,
    WorkerCapabilities,
    WorkerType,
)
from acheron.proto import synthesis_pb2, synthesis_pb2_grpc
from acheron.shell.transports._multipart import _build_result, _materialize_artifact

_MAX_WORKER_OUTPUT_BYTES = 64 * 1024 * 1024
_MAX_WORKER_ARTIFACTS = 64
_MAX_WORKER_CHUNKS = 4096
_GRPC_TIMEOUT_SECONDS = 1800.0

# Alias the proto Artifact once at module top — the proto-generated name
# is not in the mypy module's namespace, so the bare `synthesis_pb2.Artifact`
# would otherwise need a per-site # type: ignore[name-defined].
type _Artifact = synthesis_pb2.Artifact  # type: ignore[name-defined]

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
        registration_token: str | None = None,
        registration_token_provider: Callable[[], str | None] | None = None,
    ) -> None:
        self._channel = channel
        self._stub = synthesis_pb2_grpc.SynthesisStub(channel)  # type: ignore[no-untyped-call]  # proto stubs are untyped
        self._health_stub = health_pb2_grpc.HealthStub(channel)
        self._data_dir = Path(data_dir)
        self._registration_token = registration_token
        self._registration_token_provider = registration_token_provider

    def _metadata(self) -> tuple[tuple[str, str], ...]:
        token = (
            self._registration_token_provider()
            if self._registration_token_provider is not None
            else self._registration_token
        )
        return () if token is None else (("authorization", f"Bearer {token}"),)

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

    async def execute(self, job: Job) -> JobResult:  # noqa: C901, D102
        if job.job_type != WorkerType.TTS:
            msg = f"GrpcWorker only supports TTS, got {job.job_type}"
            raise WorkerError(msg)

        request = synthesis_pb2.SynthesisRequest(  # type: ignore[attr-defined]  # proto-generated class not in mypy namespace
            job_id=job.job_id,
            text=str(job.payload.get("text", "")),
            language=str(job.payload.get("language", "")),
            model=str(job.payload.get("model", "")),
        )

        start_time = time.monotonic()
        artifact_parts: list[_Artifact] = []
        pcm_chunks: list[bytes] = []
        total_bytes = 0
        total_chunks = 0

        try:
            async for chunk in self._stub.Synthesize(
                request,
                metadata=self._metadata(),
                timeout=_GRPC_TIMEOUT_SECONDS,
            ):
                total_chunks += 1
                if total_chunks > _MAX_WORKER_CHUNKS:
                    raise WorkerError("Worker returned too many output chunks")
                payload_type = chunk.WhichOneof("payload")
                if payload_type == "artifact":
                    total_bytes += len(chunk.artifact.data)
                    artifact_parts.append(chunk.artifact)
                    if len(artifact_parts) > _MAX_WORKER_ARTIFACTS:
                        raise WorkerError("Worker returned too many artifacts")
                elif payload_type == "pcm_data":
                    total_bytes += len(chunk.pcm_data)
                    pcm_chunks.append(chunk.pcm_data)
                if total_bytes > _MAX_WORKER_OUTPUT_BYTES:
                    raise WorkerError("Worker output exceeds the maximum allowed size")
        except grpc.aio.AioRpcError as exc:
            if exc.code() == grpc.StatusCode.UNAVAILABLE:
                msg = f"Worker unavailable: {exc.details()}"
                raise WorkerUnavailableError(msg) from exc
            msg = f"gRPC error {exc.code()}: {exc.details()}"
            raise WorkerError(msg) from exc

        duration = time.monotonic() - start_time

        if artifact_parts:
            return await self._assemble_artifacts(job.job_id, artifact_parts, duration)
        return await self._assemble_pcm(job.job_id, pcm_chunks, duration)

    async def _assemble_artifacts(
        self,
        job_id: str,
        artifacts: list[_Artifact],
        duration: float,
    ) -> JobResult:
        plan_job_id = "-".join(job_id.split("-")[:-1]) if "-" in job_id else job_id
        step_id = job_id.rsplit("-", maxsplit=1)[-1] if "-" in job_id else "execute"
        dest_dir = self._data_dir / plan_job_id / step_id

        if sum(len(art.data) for art in artifacts) > _MAX_WORKER_OUTPUT_BYTES:
            raise WorkerError("Worker output exceeds the maximum allowed size")
        outputs: list[OutputFile] = []
        for art in artifacts:
            out = await _materialize_artifact(
                data=art.data,
                filename=art.filename,
                content_type=art.content_type,
                dest_dir=dest_dir,
                root_dir=self._data_dir,
            )
            outputs.append(out)
        metrics = JobMetrics(duration_seconds=duration, gpu_seconds=duration)
        return _build_result(job_id=job_id, outputs=tuple(outputs), metrics=metrics)

    async def _assemble_pcm(self, job_id: str, pcm_chunks: list[bytes], duration: float) -> JobResult:
        audio_data = b"".join(pcm_chunks)
        plan_job_id = "-".join(job_id.split("-")[:-1]) if "-" in job_id else job_id
        step_id = job_id.rsplit("-", maxsplit=1)[-1] if "-" in job_id else "execute"
        output = await _materialize_artifact(
            data=audio_data,
            filename=f"{job_id}.pcm",
            content_type="audio/pcm",
            dest_dir=self._data_dir / plan_job_id / step_id,
            root_dir=self._data_dir,
        )
        return _build_result(
            job_id=job_id,
            outputs=(output,),
            metrics=JobMetrics(duration_seconds=duration, gpu_seconds=duration),
        )

    async def health(self) -> bool:  # noqa: D102
        try:
            response = await self._health_stub.Check(
                health_pb2.HealthCheckRequest(),
                metadata=self._metadata(),
                timeout=30.0,
            )
        except grpc.aio.AioRpcError:
            return False
        else:
            return response.status == health_pb2.HealthCheckResponse.SERVING  # type: ignore[no-any-return]  # proto enum value is Any-typed
