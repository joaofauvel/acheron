"""Tests for the GrpcWorker transport."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import grpc
import grpc.aio
import pytest
import pytest_asyncio
from grpc_health.v1 import health, health_pb2_grpc

from acheron.core.errors import WorkerError
from acheron.core.models import (
    BatchJob,
    Job,
    JobStatus,
    WorkerType,
)
from acheron.proto import synthesis_pb2, synthesis_pb2_grpc
from acheron.shell.transports.grpc import GrpcWorker

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class _FakeSynthesisServicer(synthesis_pb2_grpc.SynthesisServicer):
    """In-process gRPC servicer that returns canned PCM chunks."""

    def __init__(self, chunks: list[bytes] | None = None, fail: bool = False) -> None:
        self._chunks = chunks or [b"\x00\x00" * 100]
        self._fail = fail

    def Synthesize(  # noqa: N802
        self,
        request: synthesis_pb2.SynthesisRequest,  # type: ignore[name-defined]
        context: grpc.aio.ServicerContext,  # type: ignore[type-arg]
    ) -> Any:
        if self._fail:
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details("GPU down")
            return
        for chunk in self._chunks:
            yield synthesis_pb2.AudioChunk(  # type: ignore[attr-defined]
                pcm_data=chunk,
                sample_rate=22050,
                channels=1,
            )


@pytest_asyncio.fixture
async def grpc_server() -> AsyncIterator[tuple[str, _FakeSynthesisServicer]]:
    """Start an in-process gRPC server."""
    servicer = _FakeSynthesisServicer()
    server = grpc.aio.server()
    synthesis_pb2_grpc.add_SynthesisServicer_to_server(servicer, server)  # type: ignore[no-untyped-call]
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    port = server.add_insecure_port("localhost:0")
    await server.start()
    yield f"localhost:{port}", servicer
    await server.stop(0)


@pytest_asyncio.fixture
async def grpc_worker(grpc_server: tuple[str, _FakeSynthesisServicer]) -> AsyncIterator[GrpcWorker]:
    """Create a GrpcWorker connected to the in-process server."""
    addr, _ = grpc_server
    channel = grpc.aio.insecure_channel(addr)
    worker = GrpcWorker(channel)
    yield worker
    await channel.close()


class TestGrpcWorkerHealth:
    @pytest.mark.asyncio
    async def test_health_returns_true(self, grpc_worker: GrpcWorker) -> None:
        result = await grpc_worker.health()
        assert result is True

    @pytest.mark.asyncio
    async def test_health_returns_false_on_unreachable(self) -> None:
        channel = grpc.aio.insecure_channel("localhost:1")
        worker = GrpcWorker(channel)
        result = await worker.health()
        assert result is False
        await channel.close()


class TestGrpcWorkerCapabilities:
    @pytest.mark.asyncio
    async def test_capabilities_returns_tts(self, grpc_worker: GrpcWorker) -> None:
        caps = await grpc_worker.capabilities()
        assert caps.worker_type == WorkerType.TTS
        assert caps.batch_capable is True


class TestGrpcWorkerExecute:
    @pytest.mark.asyncio
    async def test_execute_assembles_pcm_chunks(self, grpc_server: tuple[str, _FakeSynthesisServicer]) -> None:
        addr, servicer = grpc_server
        servicer._chunks = [b"\x01\x02", b"\x03\x04"]  # noqa: SLF001
        channel = grpc.aio.insecure_channel(addr)
        worker = GrpcWorker(channel)
        job = Job(job_id="j-1", job_type=WorkerType.TTS, payload={"text": "hola", "language": "es"}, chapter_id="ch1")
        result = await worker.execute(job)
        assert result.status == JobStatus.SUCCESS
        assert result.job_id == "j-1"
        assert len(result.outputs) > 0
        await channel.close()

    @pytest.mark.asyncio
    async def test_execute_raises_on_non_tts_job(self, grpc_worker: GrpcWorker) -> None:
        job = Job(job_id="j-1", job_type=WorkerType.ASR, payload={}, chapter_id="ch1")
        with pytest.raises(WorkerError, match="TTS"):
            await grpc_worker.execute(job)

    @pytest.mark.asyncio
    async def test_execute_raises_on_server_error(self, grpc_server: tuple[str, _FakeSynthesisServicer]) -> None:
        addr, servicer = grpc_server
        servicer._fail = True  # noqa: SLF001
        channel = grpc.aio.insecure_channel(addr)
        worker = GrpcWorker(channel)
        job = Job(job_id="j-1", job_type=WorkerType.TTS, payload={"text": "hola"}, chapter_id="ch1")
        with pytest.raises(WorkerError):
            await worker.execute(job)
        await channel.close()


class TestGrpcWorkerBatch:
    @pytest.mark.asyncio
    async def test_submit_batch_returns_handle(self, grpc_worker: GrpcWorker) -> None:
        batch = BatchJob(
            batch_id="b-1",
            jobs=(
                Job(job_id="j-1", job_type=WorkerType.TTS, payload={"text": "hola"}, chapter_id="ch1"),
                Job(job_id="j-2", job_type=WorkerType.TTS, payload={"text": "adios"}, chapter_id="ch1"),
            ),
        )
        handle = await grpc_worker.submit_batch(batch)
        assert handle == "b-1"

    @pytest.mark.asyncio
    async def test_collect_results_returns_all(self, grpc_worker: GrpcWorker) -> None:
        batch = BatchJob(
            batch_id="b-1",
            jobs=(
                Job(job_id="j-1", job_type=WorkerType.TTS, payload={"text": "hola"}, chapter_id="ch1"),
                Job(job_id="j-2", job_type=WorkerType.TTS, payload={"text": "adios"}, chapter_id="ch1"),
            ),
        )
        await grpc_worker.submit_batch(batch)
        results = await grpc_worker.collect_results("b-1")
        assert len(results) == 2
        assert all(r.status == JobStatus.SUCCESS for r in results)
