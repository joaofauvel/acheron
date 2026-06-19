"""Tests for the gRPC stub's TLS server support."""

from __future__ import annotations

from pathlib import Path

import grpc
import grpc.aio
import pytest
from grpc.health.v1 import health_pb2, health_pb2_grpc


@pytest.mark.asyncio
async def test_grpc_server_serves_tls_when_configured(dev_certs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACHERON_TLS_CERT_FILE", str(dev_certs / "tts-grpc-stub.crt"))
    monkeypatch.setenv("ACHERON_TLS_KEY_FILE", str(dev_certs / "tts-grpc-stub.key"))
    monkeypatch.setenv("WORKER_PORT", "0")
    monkeypatch.setenv("WORKER_HTTP_PORT", "0")
    monkeypatch.setenv("ORCHESTRATOR_URL", "https://orchestrator:8000")
    monkeypatch.setenv("WORKER_ENDPOINT", "tts-grpc-stub:0")
    monkeypatch.delenv("ACHERON_REGISTRATION_TOKEN", raising=False)

    from stubs import grpc_worker_stub

    async def fake_register(endpoint: str, token: str) -> None:
        return None

    monkeypatch.setattr(grpc_worker_stub, "_register", fake_register)

    server, port = await grpc_worker_stub.create_server(register=False)
    await server.start()
    try:
        ca_pem = (dev_certs / "acheron-ca.crt").read_bytes()
        creds = grpc.ssl_channel_credentials(root_certificates=ca_pem)
        async with grpc.aio.secure_channel(f"127.0.0.1:{port}", creds) as channel:
            stub = health_pb2_grpc.HealthStub(channel)
            resp = await stub.Check(health_pb2.HealthCheckRequest())
            assert resp.status == health_pb2.HealthCheckResponse.SERVING
    finally:
        await server.stop(0)
