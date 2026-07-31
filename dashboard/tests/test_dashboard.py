"""Tests for the HTMX dashboard."""

from __future__ import annotations

import httpx
import pytest
import pytest_asyncio
import respx
from httpx import ASGITransport, AsyncClient

from dashboard.app import create_app

_ORCH_URL = "http://orchestrator:8000"


def _job_payload(job_id: str = "job-1", **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "job_id": job_id,
        "status": "running",
        "plan_id": "p1",
        "label": None,
        "retries_from": None,
        "source_type": "epub",
        "source_language": "en",
        "target_language": "es",
        "asr_model": None,
        "executor_strategy": "streaming",
        "created_at": "2026-07-29T12:00:00Z",
        "last_persisted_at": "2026-07-29T12:00:01Z",
        "progress": {
            "completed_steps": 0,
            "total_steps": 0,
            "current_step_id": None,
            "current_worker_type": None,
            "current_worker_id": None,
            "eta_seconds": None,
        },
        "total_cost": 0.0,
        "total_duration_seconds": 0.0,
        "total_cost_basis": None,
        "outputs": [],
        "errors": [],
        "warnings": [],
    }
    payload.update(overrides)
    return payload


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

    @pytest.mark.asyncio
    async def test_index_includes_yellow_status_style(self, client):
        resp = await client.get("/")
        assert ".dot-yellow" in resp.text
        assert "#d29922" in resp.text

    @pytest.mark.asyncio
    async def test_index_wires_one_second_booting_timer(self, client):
        resp = await client.get("/")
        assert "updateBootingProgress" in resp.text
        assert "setInterval(updateBootingProgress, 1000)" in resp.text
        assert resp.text.count("setInterval(") == 1


class TestJobsPartial:
    @respx.mock
    @pytest.mark.asyncio
    async def test_jobs_partial_returns_table(self, client):
        respx.get(f"{_ORCH_URL}/jobs").mock(
            return_value=httpx.Response(
                200,
                json={
                    "jobs": [
                        _job_payload(
                            progress={
                                "completed_steps": 2,
                                "total_steps": 5,
                                "current_step_id": None,
                                "current_worker_type": None,
                                "current_worker_id": None,
                                "eta_seconds": None,
                            }
                        )
                    ]
                },
            )
        )
        resp = await client.get("/partials/jobs")
        assert resp.status_code == 200
        assert "job-1" in resp.text
        assert "running" in resp.text
        assert "execution-time evidence" in resp.text

    @respx.mock
    @pytest.mark.asyncio
    async def test_jobs_partial_empty(self, client):
        respx.get(f"{_ORCH_URL}/jobs").mock(return_value=httpx.Response(200, json={"jobs": []}))
        resp = await client.get("/partials/jobs")
        assert resp.status_code == 200
        assert "No jobs" in resp.text

    @respx.mock
    @pytest.mark.asyncio
    async def test_jobs_partial_links_failed_job(self, client):
        payload = _job_payload(status="failed")
        payload["errors"] = [
            {
                "step_id": "step-2",
                "worker_type": "tts",
                "worker_id": "tts-1",
                "message": "older failure",
                "timestamp": "2026-07-29T12:00:01Z",
            },
            {
                "step_id": "step-3",
                "worker_type": "tts",
                "worker_id": "tts-1",
                "message": "malformed audio",
                "timestamp": "2026-07-29T12:00:02Z",
            },
        ]
        respx.get(f"{_ORCH_URL}/jobs").mock(return_value=httpx.Response(200, json={"jobs": [payload]}))
        resp = await client.get("/partials/jobs")
        assert resp.status_code == 200
        assert 'href="/partials/jobs/job-1"' in resp.text
        assert "last error" in resp.text.lower()
        assert "malformed audio" in resp.text
        assert "older failure" not in resp.text


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
    async def test_successful_cost_summary_still_renders_authoritative_job_rows(self, client):
        respx.get(f"{_ORCH_URL}/cost", params={"window": "7d"}).mock(
            return_value=httpx.Response(
                200,
                json={
                    "window": "7d",
                    "since": "2026-07-22T12:00:00Z",
                    "until": "2026-07-29T12:00:00Z",
                    "total_cost": 0.42,
                    "job_count": 1,
                    "unknown_cost_jobs": 0,
                },
            )
        )
        respx.get(f"{_ORCH_URL}/jobs").mock(
            return_value=httpx.Response(
                200,
                json={
                    "jobs": [
                        _job_payload(
                            status="completed",
                            total_cost=0.42,
                            total_cost_basis="measured",
                            progress={
                                "completed_steps": 5,
                                "total_steps": 5,
                                "current_step_id": None,
                                "current_worker_type": None,
                                "current_worker_id": None,
                                "eta_seconds": None,
                            },
                        )
                    ]
                },
            )
        )
        resp = await client.get("/partials/cost")
        assert resp.status_code == 200
        assert "Estimated cost, last 7d" in resp.text
        assert "job-1" in resp.text
        assert "$0.42" in resp.text
        assert {str(call.request.url) for call in respx.calls} == {
            f"{_ORCH_URL}/cost?window=7d",
            f"{_ORCH_URL}/jobs",
        }

    @respx.mock
    @pytest.mark.asyncio
    async def test_cost_partial_empty(self, client):
        respx.get(f"{_ORCH_URL}/cost").mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{_ORCH_URL}/jobs").mock(return_value=httpx.Response(200, json={"jobs": []}))
        resp = await client.get("/partials/cost")
        assert resp.status_code == 200
        assert "No cost" in resp.text


class TestForwardAuth:
    @pytest.mark.asyncio
    async def test_reads_forwarded_user_header(self, client, monkeypatch: pytest.MonkeyPatch):
        """When reverse proxy trust is enabled, the X-Forwarded-User header is rendered."""
        monkeypatch.setenv("ACHERON_TRUST_REVERSE_PROXY", "1")
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
    async def test_status_forwards_readiness_fragment_unchanged(self, client):
        fragment = '<span class="dot dot-yellow"></span> Waiting (1/3 TTS healthy)'
        respx.get(f"{_ORCH_URL}/partials/status").mock(return_value=httpx.Response(200, text=fragment))
        resp = await client.get("/partials/status")
        assert resp.status_code == 200
        assert resp.text == fragment

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
        assert "data-booting-progress" not in resp.text

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
                            "booting_elapsed_seconds": 182.0,
                            "booting_timeout_seconds": 600.0,
                            "last_error": "cold start: connection refused",
                        },
                    ]
                },
            )
        )
        resp = await client.get("/partials/workers")
        assert resp.status_code == 200
        assert "badge-booting" in resp.text
        assert "182s / 600s" in resp.text
        assert '<progress value="182" max="600"' in resp.text
        assert 'data-booting-progress="true"' in resp.text
        assert 'data-elapsed-seconds="182"' in resp.text
        assert 'data-timeout-seconds="600"' in resp.text
        assert "View Error" in resp.text
        assert "cold start: connection refused" in resp.text

    @respx.mock
    @pytest.mark.asyncio
    async def test_booting_worker_clamps_server_rendered_progress(self, client):
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
                            "booting_elapsed_seconds": 900.0,
                            "booting_timeout_seconds": 600.0,
                            "last_error": None,
                        },
                    ]
                },
            )
        )
        resp = await client.get("/partials/workers")
        assert resp.status_code == 200
        assert "600s / 600s" in resp.text
        assert 'data-elapsed-seconds="600"' in resp.text
        assert 'data-percentage="100"' in resp.text
        assert '<progress value="600" max="600"' in resp.text

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
        assert "data-booting-progress" not in resp.text
        assert "View Error" in resp.text
