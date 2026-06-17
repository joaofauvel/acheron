"""Tests for worker API routes."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from acheron.shell.api.app import create_app
from acheron.shell.cache import PlanCache
from acheron.shell.registry import WorkerRegistry


@pytest.fixture
def app(tmp_path):  # type: ignore[no-untyped-def]
    return create_app(registry=WorkerRegistry(), cache=PlanCache(tmp_path), data_dir=tmp_path)


@pytest_asyncio.fixture
async def client(app):  # type: ignore[no-untyped-def]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestWorkerRoutes:
    @pytest.mark.asyncio
    async def test_register_worker(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.post(
            "/workers",
            json={
                "worker_id": "tts-1",
                "endpoint": "http://tts:8000",
                "transport": "http",
                "capabilities": {
                    "worker_type": "tts",
                    "supported_languages_in": ["en", "es"],
                    "supported_languages_out": ["en", "es"],
                    "batch_capable": True,
                },
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["worker_id"] == "tts-1"
        assert data["worker_type"] == "tts"

    @pytest.mark.asyncio
    async def test_register_worker_invalid_type(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.post(
            "/workers",
            json={
                "worker_id": "bad-1",
                "endpoint": "http://bad:8000",
                "transport": "http",
                "capabilities": {
                    "worker_type": "invalid_type",
                    "supported_languages_in": ["en"],
                    "supported_languages_out": ["es"],
                },
            },
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_list_workers(self, client) -> None:  # type: ignore[no-untyped-def]
        await client.post(
            "/workers",
            json={
                "worker_id": "tts-1",
                "endpoint": "http://tts:8000",
                "transport": "http",
                "capabilities": {
                    "worker_type": "tts",
                    "supported_languages_in": ["en"],
                    "supported_languages_out": ["es"],
                },
            },
        )
        response = await client.get("/workers")
        assert response.status_code == 200
        assert len(response.json()["workers"]) == 1
