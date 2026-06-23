"""Tests for the GrpcWorker transport."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

import grpc
import grpc.aio
import pytest
import pytest_asyncio
from grpc_health.v1 import health, health_pb2, health_pb2_grpc

from acheron.core.errors import WorkerError, WorkerUnavailableError
from acheron.core.models import (
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
        context: grpc.aio.ServicerContext,
    ) -> Any:
        if self._fail:
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details("GPU down")
            return
        for chunk in self._chunks:
            yield synthesis_pb2.OutputChunk(  # type: ignore[attr-defined]
                pcm_data=chunk,
                sample_rate=22050,
                channels=1,
            )


class _ArtifactServicer(synthesis_pb2_grpc.SynthesisServicer):
    """In-process gRPC servicer that emits Artifact-mode OutputChunks."""

    def __init__(
        self,
        artifacts: list[synthesis_pb2.Artifact] | None = None,  # type: ignore[name-defined]
    ) -> None:
        self._artifacts = artifacts or []

    def Synthesize(  # noqa: N802
        self,
        request: synthesis_pb2.SynthesisRequest,  # type: ignore[name-defined]
        context: grpc.aio.ServicerContext,
    ) -> Any:
        for art in self._artifacts:
            yield synthesis_pb2.OutputChunk(artifact=art)  # type: ignore[attr-defined]


@pytest_asyncio.fixture
async def grpc_server() -> AsyncIterator[tuple[str, _FakeSynthesisServicer]]:
    """Start an in-process gRPC server."""
    servicer = _FakeSynthesisServicer()
    server = grpc.aio.server()
    synthesis_pb2_grpc.add_SynthesisServicer_to_server(servicer, server)  # type: ignore[no-untyped-call]
    health_servicer = health.HealthServicer()
    health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)
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
    worker = GrpcWorker(channel, data_dir=Path("/tmp/acheron-grpc-test"))
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
        worker = GrpcWorker(channel, data_dir=Path("/tmp/acheron-grpc-test"))
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
        worker = GrpcWorker(channel, data_dir=Path("/tmp/acheron-grpc-test"))
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
    async def test_execute_raises_unavailable_on_server_error(
        self, grpc_server: tuple[str, _FakeSynthesisServicer]
    ) -> None:
        addr, servicer = grpc_server
        servicer._fail = True  # noqa: SLF001
        channel = grpc.aio.insecure_channel(addr)
        worker = GrpcWorker(channel, data_dir=Path("/tmp/acheron-grpc-test"))
        job = Job(job_id="j-1", job_type=WorkerType.TTS, payload={"text": "hola"}, chapter_id="ch1")
        with pytest.raises(WorkerUnavailableError, match="unavailable"):
            await worker.execute(job)
        await channel.close()


class TestGrpcWorkerExecuteArtifact:
    @pytest.mark.asyncio
    async def test_assembles_artifacts(self, tmp_path: Path) -> None:
        servicer = _ArtifactServicer(
            artifacts=[
                synthesis_pb2.Artifact(  # type: ignore[attr-defined]
                    filename="ch1_0000.wav", content_type="audio/wav", data=b"\x01\x02\x03"
                ),
                synthesis_pb2.Artifact(  # type: ignore[attr-defined]
                    filename="ch1_0001.wav", content_type="audio/wav", data=b"\x04\x05\x06"
                ),
            ]
        )
        server = grpc.aio.server()
        synthesis_pb2_grpc.add_SynthesisServicer_to_server(servicer, server)  # type: ignore[no-untyped-call]
        health_servicer = health.HealthServicer()
        health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)
        health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
        port = server.add_insecure_port("localhost:0")
        await server.start()
        try:
            channel = grpc.aio.insecure_channel(f"localhost:{port}")
            worker = GrpcWorker(channel, data_dir=tmp_path)
            job = Job(
                job_id="job-xyz-synthesize-ch1",
                job_type=WorkerType.TTS,
                payload={"text": "hi"},
                chapter_id="ch1",
            )
            result = await worker.execute(job)
            assert result.status == JobStatus.SUCCESS
            assert len(result.outputs) == 2
            assert result.outputs[0].filename == "ch1_0000.wav"
            assert Path(result.outputs[0].path).read_bytes() == b"\x01\x02\x03"
        finally:
            await server.stop(0)


@pytest.mark.asyncio
async def test_grpc_channel_uses_secure_when_ca_set(dev_certs: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: PLR0915
    """When ACHERON_TLS_CA_FILE is set, the channel must reject servers whose
    cert is not signed by that CA. This proves the secure path is wired up
    and cert verification is on.
    """
    import datetime
    import ipaddress
    import socket as _socket
    import time as _time

    import grpc
    import grpc.aio
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from grpc_health.v1 import health, health_pb2, health_pb2_grpc

    monkeypatch.setenv("ACHERON_TLS_CA_FILE", str(dev_certs / "acheron-ca.crt"))

    # Generate a self-signed cert NOT signed by the Acheron CA.
    bogus_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    bogus_cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "bogus")]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "bogus")]))
        .public_key(bogus_key.public_key())
        .serial_number(1)
        .not_valid_before(datetime.datetime.now(datetime.UTC))
        .not_valid_after(datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .sign(bogus_key, hashes.SHA256())
    )
    bogus_cert_pem = bogus_cert.public_bytes(serialization.Encoding.PEM)
    bogus_key_pem = bogus_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )

    with _socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    async def serve() -> None:
        server = grpc.aio.server()
        health_servicer = health.HealthServicer()
        health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)
        health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
        creds = grpc.ssl_server_credentials([(bogus_key_pem, bogus_cert_pem)])
        server.add_secure_port(f"127.0.0.1:{port}", creds)
        await server.start()
        try:
            await server.wait_for_termination()
        finally:
            await server.stop(0)

    task = asyncio.create_task(serve())
    deadline = _time.monotonic() + 3
    while _time.monotonic() < deadline:
        try:
            with _socket.create_connection(("127.0.0.1", port), timeout=0.1):
                break
        except OSError:
            await asyncio.sleep(0.05)

    try:
        from acheron.shell.tls import grpc_channel

        channel = grpc_channel(f"127.0.0.1:{port}")
        try:
            stub = health_pb2_grpc.HealthStub(channel)
            with pytest.raises(grpc.aio.AioRpcError) as exc_info:
                await stub.Check(health_pb2.HealthCheckRequest(), timeout=2)  # type: ignore[attr-defined]
            assert exc_info.value.code() in (
                grpc.StatusCode.UNAVAILABLE,
                grpc.StatusCode.DEADLINE_EXCEEDED,
            )
        finally:
            await channel.close()
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
