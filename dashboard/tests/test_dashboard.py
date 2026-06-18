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
        assert "htmx" in resp.text.lower()


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
