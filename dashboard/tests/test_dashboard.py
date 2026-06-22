"""Tests for the HTMX dashboard."""

from __future__ import annotations

import httpx
import pytest
import pytest_asyncio
import respx
from httpx import ASGITransport, AsyncClient

from dashboard.app import create_app

_ORCH_URL = "http://orchestrator:8000"


@pytest.fixture
def app():
    return create_app(orchestrator_url=_ORCH_URL)


@pytest_asyncio.fixture()
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestEnvConfig:
    @respx.mock
    @pytest.mark.asyncio
    async def test_reads_acheron_url_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Dashboard uses ACHERON_URL env var when no orchestrator_url is passed."""
        target = "http://orch-from-env:9999"
        monkeypatch.setenv("ACHERON_URL", target)
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            respx.get(f"{target}/jobs").mock(return_value=httpx.Response(200, json={"jobs": []}))
            resp = await client.get("/partials/jobs")
            assert resp.status_code == 200
            assert respx.calls.call_count == 1
            assert str(respx.calls[0].request.url) == f"{target}/jobs"

    def test_explicit_url_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ACHERON_URL", "http://env-host:1111")
        app = create_app(orchestrator_url="http://explicit:2222")
        assert app is not None


class TestIndexPage:
    @pytest.mark.asyncio
    async def test_index_returns_200(self, client):
        resp = await client.get("/")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_index_contains_jobs_section(self, client):
        resp = await client.get("/")
        assert 'id="jobs"' in resp.text

    @pytest.mark.asyncio
    async def test_index_contains_workers_section(self, client):
        resp = await client.get("/")
        assert 'id="workers"' in resp.text

    @pytest.mark.asyncio
    async def test_index_contains_cost_section(self, client):
        resp = await client.get("/")
        assert 'id="cost"' in resp.text

    @pytest.mark.asyncio
    async def test_index_includes_htmx(self, client):
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "htmx" in resp.text.lower()

    @pytest.mark.asyncio
    async def test_index_contains_status_indicator(self, client):
        resp = await client.get("/")
        assert resp.status_code == 200
        assert 'id="status"' in resp.text
        assert "/partials/status" in resp.text


class TestJobsPartial:
    @respx.mock
    @pytest.mark.asyncio
    async def test_jobs_partial_returns_table(self, client):
        respx.get(f"{_ORCH_URL}/jobs").mock(
            return_value=httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "job_id": "job-1",
                            "status": "running",
                            "plan_id": "p1",
                            "completed_steps": 2,
                            "total_steps": 5,
                            "errors": [],
                        },
                    ]
                },
            )
        )
        resp = await client.get("/partials/jobs")
        assert resp.status_code == 200
        assert "job-1" in resp.text
        assert "running" in resp.text

    @respx.mock
    @pytest.mark.asyncio
    async def test_jobs_partial_empty(self, client):
        respx.get(f"{_ORCH_URL}/jobs").mock(return_value=httpx.Response(200, json={"jobs": []}))
        resp = await client.get("/partials/jobs")
        assert resp.status_code == 200
        assert "No jobs" in resp.text


class TestWorkersPartial:
    @respx.mock
    @pytest.mark.asyncio
    async def test_workers_partial_returns_table(self, client):
        respx.get(f"{_ORCH_URL}/workers").mock(
            return_value=httpx.Response(
                200,
                json={
                    "workers": [
                        {
                            "worker_id": "tts-1",
                            "worker_type": "tts",
                            "endpoint": "http://tts:8000",
                            "transport": "http",
                            "consecutive_failures": 0,
                        },
                    ]
                },
            )
        )
        resp = await client.get("/partials/workers")
        assert resp.status_code == 200
        assert "tts-1" in resp.text
        assert "tts" in resp.text

    @respx.mock
    @pytest.mark.asyncio
    async def test_workers_partial_empty(self, client):
        respx.get(f"{_ORCH_URL}/workers").mock(return_value=httpx.Response(200, json={"workers": []}))
        resp = await client.get("/partials/workers")
        assert resp.status_code == 200
        assert "No workers" in resp.text


class TestCostPartial:
    @respx.mock
    @pytest.mark.asyncio
    async def test_cost_partial_returns_table(self, client):
        respx.get(f"{_ORCH_URL}/jobs").mock(
            return_value=httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "job_id": "job-1",
                            "status": "completed",
                            "plan_id": "p1",
                            "completed_steps": 5,
                            "total_steps": 5,
                            "errors": [],
                        },
                    ]
                },
            )
        )
        resp = await client.get("/partials/cost")
        assert resp.status_code == 200
        assert "job-1" in resp.text

    @respx.mock
    @pytest.mark.asyncio
    async def test_cost_partial_empty(self, client):
        respx.get(f"{_ORCH_URL}/jobs").mock(return_value=httpx.Response(200, json={"jobs": []}))
        resp = await client.get("/partials/cost")
        assert resp.status_code == 200
        assert "No cost" in resp.text


class TestForwardAuth:
    @pytest.mark.asyncio
    async def test_reads_forwarded_user_header(self, client):
        resp = await client.get("/", headers={"X-Forwarded-User": "admin"})
        assert resp.status_code == 200
        assert "admin" in resp.text

    @pytest.mark.asyncio
    async def test_works_without_auth_header(self, client):
        resp = await client.get("/")
        assert resp.status_code == 200


class TestErrorHandling:
    @respx.mock
    @pytest.mark.asyncio
    async def test_jobs_partial_returns_empty_on_connection_error(self, client):
        respx.get(f"{_ORCH_URL}/jobs").mock(side_effect=httpx.ConnectError("refused"))
        resp = await client.get("/partials/jobs")
        assert resp.status_code == 200
        assert "No jobs" in resp.text

    @respx.mock
    @pytest.mark.asyncio
    async def test_workers_partial_returns_empty_on_connection_error(self, client):
        respx.get(f"{_ORCH_URL}/workers").mock(side_effect=httpx.ConnectError("refused"))
        resp = await client.get("/partials/workers")
        assert resp.status_code == 200
        assert "No workers" in resp.text

    @respx.mock
    @pytest.mark.asyncio
    async def test_jobs_partial_returns_empty_on_oserror(self, client):
        """An OSError (e.g. DNS failure) must also fall through to the empty state."""
        respx.get(f"{_ORCH_URL}/jobs").mock(side_effect=OSError("name resolution failed"))
        resp = await client.get("/partials/jobs")
        assert resp.status_code == 200
        assert "No jobs" in resp.text

    @respx.mock
    @pytest.mark.asyncio
    async def test_status_partial_disconnected_on_oserror(self, client):
        respx.get(f"{_ORCH_URL}/partials/status").mock(side_effect=OSError("network down"))
        resp = await client.get("/partials/status")
        assert resp.status_code == 200
        assert "Disconnected" in resp.text


class TestStatusPartial:
    @respx.mock
    @pytest.mark.asyncio
    async def test_status_connected_when_orchestrator_up(self, client):
        respx.get(f"{_ORCH_URL}/partials/status").mock(
            return_value=httpx.Response(200, text='<span class="dot dot-green"></span> Connected')
        )
        resp = await client.get("/partials/status")
        assert resp.status_code == 200
        assert "Connected" in resp.text
        assert "dot-green" in resp.text

    @respx.mock
    @pytest.mark.asyncio
    async def test_status_disconnected_when_orchestrator_down(self, client):
        respx.get(f"{_ORCH_URL}/partials/status").mock(side_effect=httpx.ConnectError("refused"))
        resp = await client.get("/partials/status")
        assert resp.status_code == 200
        assert "Disconnected" in resp.text
        assert "dot-red" in resp.text


class TestWorkersPartialStatus:
    @respx.mock
    @pytest.mark.asyncio
    async def test_healthy_worker_shows_healthy_badge(self, client):
        respx.get(f"{_ORCH_URL}/workers").mock(
            return_value=httpx.Response(
                200,
                json={
                    "workers": [
                        {
                            "worker_id": "tts-1",
                            "worker_type": "tts",
                            "endpoint": "http://tts:8000",
                            "transport": "http",
                            "consecutive_failures": 0,
                            "status": "healthy",
                            "last_error": None,
                        },
                    ]
                },
            )
        )
        resp = await client.get("/partials/workers")
        assert resp.status_code == 200
        assert "badge-healthy" in resp.text

    @respx.mock
    @pytest.mark.asyncio
    async def test_booting_worker_shows_booting_badge_and_error(self, client):
        respx.get(f"{_ORCH_URL}/workers").mock(
            return_value=httpx.Response(
                200,
                json={
                    "workers": [
                        {
                            "worker_id": "tts-2",
                            "worker_type": "tts",
                            "endpoint": "http://tts:8000",
                            "transport": "http",
                            "consecutive_failures": 0,
                            "status": "booting",
                            "last_error": "cold start: connection refused",
                        },
                    ]
                },
            )
        )
        resp = await client.get("/partials/workers")
        assert resp.status_code == 200
        assert "badge-booting" in resp.text
        assert "View Error" in resp.text
        assert "cold start: connection refused" in resp.text

    @respx.mock
    @pytest.mark.asyncio
    async def test_offline_worker_shows_offline_badge(self, client):
        respx.get(f"{_ORCH_URL}/workers").mock(
            return_value=httpx.Response(
                200,
                json={
                    "workers": [
                        {
                            "worker_id": "tts-3",
                            "worker_type": "tts",
                            "endpoint": "http://tts:8000",
                            "transport": "http",
                            "consecutive_failures": 2,
                            "status": "offline",
                            "last_error": "HTTP 503",
                        },
                    ]
                },
            )
        )
        resp = await client.get("/partials/workers")
        assert resp.status_code == 200
        assert "badge-offline" in resp.text
        assert "View Error" in resp.text
