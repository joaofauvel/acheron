"""Tests for the translation stub."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from stubs.translation_stub import create_app


@pytest.fixture
def translation_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKER_TYPE", "TRANSLATION")
    monkeypatch.setenv("WORKER_ENDPOINT", "http://translation-stub:8003")
    monkeypatch.setenv("ORCHESTRATOR_URL", "http://orchestrator:8000")
    monkeypatch.setenv("WORKER_PORT", "8003")
    monkeypatch.setenv("ACHERON_REGISTRATION_TOKEN", "dev-registration-token")


@pytest.mark.asyncio
async def test_health_returns_200(translation_env: None) -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_submit_returns_translated_text(translation_env: None) -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/submit",
            json={"job_id": "t-1", "payload": {"text": "hello", "source_language": "en", "target_language": "es"}},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert "translated" in data["output_data"].lower()


@pytest.mark.asyncio
async def test_self_registers_on_startup(translation_env: None) -> None:
    with patch("stubs.translation_stub.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_response = AsyncMock()
        mock_response.status_code = 201
        mock_response.raise_for_status = MagicMock()
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
        body = call_args[1]["json"]
        assert body["capabilities"]["worker_type"] == "translation"
