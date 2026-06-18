"""Tests for the stub gRPC TTS worker."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import grpc
import grpc.aio
import pytest
import pytest_asyncio

from acheron.proto import synthesis_pb2, synthesis_pb2_grpc
from stubs.grpc_worker_stub import create_server

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest_asyncio.fixture
async def grpc_stub_server() -> AsyncIterator[tuple[str, None]]:
    """Start the stub gRPC server without registration."""
    server, port = await create_server(port=0, register=False)
    await server.start()
    yield f"localhost:{port}", None
    await server.stop(0)


@pytest.mark.asyncio
async def test_synthesize_returns_pcm_chunks(grpc_stub_server: tuple[str, None]) -> None:
    addr, _ = grpc_stub_server
    async with grpc.aio.insecure_channel(addr) as channel:
        stub = synthesis_pb2_grpc.SynthesisStub(channel)
        chunks = [
            chunk
            async for chunk in stub.Synthesize(
                synthesis_pb2.SynthesisRequest(job_id="test-1", text="hello", language="en")
            )
        ]
    assert len(chunks) > 0
    assert all(c.pcm_data for c in chunks)
    assert all(c.sample_rate > 0 for c in chunks)


@pytest.mark.asyncio
async def test_synthesize_returns_silence(grpc_stub_server: tuple[str, None]) -> None:
    addr, _ = grpc_stub_server
    async with grpc.aio.insecure_channel(addr) as channel:
        stub = synthesis_pb2_grpc.SynthesisStub(channel)
        chunks = [
            chunk async for chunk in stub.Synthesize(synthesis_pb2.SynthesisRequest(job_id="test-1", text="hello"))
        ]
    for chunk in chunks:
        assert all(b == 0 for b in chunk.pcm_data)


@pytest.mark.asyncio
async def test_self_registers_on_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify the stub registers with the orchestrator on startup."""
    monkeypatch.setenv("WORKER_TYPE", "TTS")
    monkeypatch.setenv("WORKER_ENDPOINT", "http://tts-grpc-stub:9001")
    monkeypatch.setenv("ORCHESTRATOR_URL", "http://orchestrator:8000")
    monkeypatch.setenv("WORKER_PORT", "9001")
    monkeypatch.setenv("ACHERON_REGISTRATION_TOKEN", "dev-registration-token")

    with patch("stubs.grpc_worker_stub.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_response = AsyncMock()
        mock_response.status_code = 201
        mock_response.raise_for_status = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        server, _port = await create_server(port=0)
        await server.start()

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert "/workers" in call_args[0][0]
        body = call_args[1]["json"]
        assert body["transport"] == "grpc"
        assert body["endpoint"] == "tts-grpc-stub:9001"

        await server.stop(0)
