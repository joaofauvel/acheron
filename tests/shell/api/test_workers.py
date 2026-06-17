"""Tests for worker API routes."""

import pytest


class TestWorkerRoutes:
    @pytest.mark.asyncio
    async def test_register_worker(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.post(
            "/workers",
            json={
                "worker_id": "asr-1",
                "endpoint": "http://asr:8000",
                "transport": "http",
                "capabilities": {
                    "worker_type": "asr",
                    "supported_languages_in": ["en"],
                    "supported_languages_out": ["en"],
                    "metadata": {"vram_gb": 8},
                },
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["worker_id"] == "asr-1"
        assert data["worker_type"] == "asr"

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
        response = await client.get("/workers")
        assert response.status_code == 200
        initial_count = len(response.json()["workers"])

        await client.post(
            "/workers",
            json={
                "worker_id": "asr-1",
                "endpoint": "http://asr:8000",
                "transport": "http",
                "capabilities": {
                    "worker_type": "asr",
                    "supported_languages_in": ["en"],
                    "supported_languages_out": ["en"],
                },
            },
        )
        response = await client.get("/workers")
        assert response.status_code == 200
        assert len(response.json()["workers"]) == initial_count + 1
