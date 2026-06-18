"""Tests for the stub worker app."""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from stubs.worker_stub import create_app


@pytest.fixture
def tts_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKER_TYPE", "TTS")
    monkeypatch.setenv("WORKER_ENDPOINT", "http://tts-stub:8001")
    monkeypatch.setenv("ORCHESTRATOR_URL", "http://orchestrator:8000")
    monkeypatch.setenv("WORKER_PORT", "8001")
    monkeypatch.setenv("ACHERON_REGISTRATION_TOKEN", "dev-registration-token")


@pytest.fixture
def asr_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKER_TYPE", "ASR")
    monkeypatch.setenv("WORKER_ENDPOINT", "http://asr-stub:8002")
    monkeypatch.setenv("ORCHESTRATOR_URL", "http://orchestrator:8000")
    monkeypatch.setenv("WORKER_PORT", "8002")
    monkeypatch.setenv("ACHERON_REGISTRATION_TOKEN", "dev-registration-token")


@pytest.mark.asyncio
async def test_health_returns_200(tts_env: None) -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_tts_submit_returns_wav(tts_env: None) -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/submit", json={"job_id": "test-1", "payload": {"text": "hello"}})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    audio_bytes = base64.b64decode(data["output_data"])
    assert audio_bytes[:4] == b"RIFF"


@pytest.mark.asyncio
async def test_asr_submit_returns_text(asr_env: None) -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/submit", json={"job_id": "test-1", "payload": {"audio": "base64data"}})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert "mock transcription" in data["output_data"]


@pytest.mark.asyncio
async def test_self_registers_on_startup(tts_env: None) -> None:
    """Verify the app attempts to register with the orchestrator on startup."""
    with patch("stubs.worker_stub.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_response = AsyncMock()
        mock_response.status_code = 201
        mock_response.raise_for_status = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        app = create_app()
        async with app.router.lifespan_context(app):
            pass

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert "/workers" in call_args[0][0]
        assert call_args[1]["headers"]["Authorization"] == "Bearer dev-registration-token"
