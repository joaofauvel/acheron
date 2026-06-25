"""Tests for worker API routes."""

from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

_WORKER_PAYLOAD: dict[str, Any] = {
    "worker_id": "asr-1",
    "endpoint": "http://asr:8000",
    "transport": "http",
    "capabilities": {
        "worker_type": "asr",
        "supported_languages_in": ["en"],
        "supported_languages_out": ["en"],
        "metadata": {"vram_gb": 8},
    },
}


class TestWorkerRoutes:
    @pytest.mark.asyncio
    async def test_register_worker(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.post("/workers", json=_WORKER_PAYLOAD)
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

        await client.post("/workers", json=_WORKER_PAYLOAD)
        response = await client.get("/workers")
        assert response.status_code == 200
        assert len(response.json()["workers"]) == initial_count + 1

    @pytest.mark.asyncio
    async def test_list_workers_includes_status_and_last_error(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get("/workers")
        assert response.status_code == 200
        for w in response.json()["workers"]:
            assert "status" in w
            assert "last_error" in w

    @pytest.mark.asyncio
    async def test_registered_worker_defaults_to_healthy(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.post("/workers", json=_WORKER_PAYLOAD)
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "healthy"
        assert data["last_error"] is None

    @pytest.mark.asyncio
    async def test_list_workers_unauthenticated_scrubs_last_error(self, client_with_token: AsyncClient) -> None:
        """SEC-010: an unauthenticated GET /workers must not return
        ``last_error`` (it can embed internal IPs / ports / DNS detail)."""
        await client_with_token.post(
            "/workers",
            json=_WORKER_PAYLOAD,
            headers={"Authorization": "Bearer test-registration-token-must-be-32-chars-or-more"},
        )
        response = await client_with_token.get("/workers")
        assert response.status_code == 200
        for w in response.json()["workers"]:
            assert w["last_error"] is None

    @pytest.mark.asyncio
    async def test_list_workers_authenticated_includes_last_error(self, client_with_token: AsyncClient) -> None:
        """SEC-010: with a valid registration token, ``last_error`` is returned."""
        await client_with_token.post(
            "/workers",
            json=_WORKER_PAYLOAD,
            headers={"Authorization": "Bearer test-registration-token-must-be-32-chars-or-more"},
        )
        response = await client_with_token.get(
            "/workers",
            headers={"Authorization": "Bearer test-registration-token-must-be-32-chars-or-more"},
        )
        assert response.status_code == 200
        for w in response.json()["workers"]:
            assert "last_error" in w


class TestRegistrationSecurity:
    @pytest.mark.asyncio
    async def test_register_with_valid_token(self, client_with_token) -> None:  # type: ignore[no-untyped-def]
        response = await client_with_token.post(
            "/workers",
            json=_WORKER_PAYLOAD,
            headers={"Authorization": "Bearer test-registration-token-must-be-32-chars-or-more"},
        )
        assert response.status_code == 201

    @pytest.mark.asyncio
    async def test_register_without_token_rejected(self, client_with_token) -> None:  # type: ignore[no-untyped-def]
        response = await client_with_token.post("/workers", json=_WORKER_PAYLOAD)
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_register_with_wrong_token_rejected(self, client_with_token) -> None:  # type: ignore[no-untyped-def]
        response = await client_with_token.post(
            "/workers",
            json=_WORKER_PAYLOAD,
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_register_without_token_env_rejected_by_default(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When ACHERON_REGISTRATION_TOKEN is unset, registration is rejected
        unless ACHERON_OPEN_REGISTRATION=1 is explicitly set."""
        monkeypatch.delenv("ACHERON_REGISTRATION_TOKEN", raising=False)
        monkeypatch.delenv("ACHERON_OPEN_REGISTRATION", raising=False)
        from tests.shell.conftest import make_app

        app = await make_app(tmp_path)
        await app.state.orchestrator.start()
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                response = await c.post("/workers", json=_WORKER_PAYLOAD)
        finally:
            await app.state.orchestrator.shutdown()
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_register_without_token_env_opt_in_open(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """ACHERON_OPEN_REGISTRATION=1 explicitly opens registration."""
        monkeypatch.delenv("ACHERON_REGISTRATION_TOKEN", raising=False)
        monkeypatch.setenv("ACHERON_OPEN_REGISTRATION", "1")
        from tests.shell.conftest import make_app

        app = await make_app(tmp_path)
        await app.state.orchestrator.start()
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                response = await c.post("/workers", json=_WORKER_PAYLOAD)
        finally:
            await app.state.orchestrator.shutdown()
        assert response.status_code == 201


class TestStrictRequest:
    @pytest.mark.asyncio
    async def test_register_rejects_extra_fields(self, client) -> None:  # type: ignore[no-untyped-def]
        """WorkerRegistrationRequest must reject unknown fields so client typos fail loudly."""
        payload = {
            **_WORKER_PAYLOAD,
            "endpoint_typo": "http://extra:8000",
        }
        response = await client.post("/workers", json=payload)
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_capabilities_rejects_extra_fields(self, client) -> None:  # type: ignore[no-untyped-def]
        """WorkerCapabilitiesRequest must reject unknown fields inside capabilities."""
        caps: dict[str, object] = {**_WORKER_PAYLOAD["capabilities"]}
        caps["batch_capabel"] = True  # typo: missing 'e'
        payload: dict[str, object] = {**_WORKER_PAYLOAD}
        payload["capabilities"] = caps
        response = await client.post("/workers", json=payload)
        assert response.status_code == 422
