"""Dashboard job-detail and output proxy tests."""

from __future__ import annotations

import httpx
import pytest
import pytest_asyncio
import respx
from httpx import ASGITransport, AsyncClient

from dashboard.app import create_app

_ORCH_URL = "http://orchestrator:8000"


def _job_payload() -> dict[str, object]:
    return {
        "job_id": "job-1",
        "status": "failed",
        "plan_id": "plan-1",
        "label": "atlas-ch1",
        "retries_from": None,
        "source_type": "audio",
        "source_language": "en",
        "target_language": "es",
        "asr_model": "whisper-v3",
        "executor_strategy": "streaming",
        "created_at": "2026-07-29T12:00:00Z",
        "last_persisted_at": "2026-07-29T12:00:05Z",
        "progress": {
            "completed_steps": 2,
            "total_steps": 5,
            "current_step_id": "step-3",
            "current_worker_type": "tts",
            "current_worker_id": "tts-1",
            "eta_seconds": 12.5,
        },
        "total_cost": 0.0,
        "total_duration_seconds": 4.5,
        "total_cost_basis": None,
        "outputs": [
            {
                "path": "/data/jobs/job-1/result.m4b",
                "filename": "result.m4b",
                "size_bytes": 5,
                "content_type": "audio/mp4",
            }
        ],
        "errors": [
            {
                "step_id": "step-3",
                "worker_type": "tts",
                "worker_id": "tts-1",
                "message": "malformed audio",
                "timestamp": "2026-07-29T12:00:04Z",
            }
        ],
        "warnings": [],
    }


@pytest.fixture
def app():
    return create_app(orchestrator_url=_ORCH_URL)


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@respx.mock
@pytest.mark.asyncio
async def test_job_detail_renders_outputs_and_step_error(client: AsyncClient) -> None:
    respx.get(f"{_ORCH_URL}/jobs/job-1").mock(return_value=httpx.Response(200, json=_job_payload()))

    response = await client.get("/partials/jobs/job-1")

    assert response.status_code == 200
    for value in (
        "plan-1",
        "atlas-ch1",
        "audio",
        "en",
        "es",
        "whisper-v3",
        "streaming",
        "2026-07-29T12:00:00Z",
        "2026-07-29T12:00:05Z",
        "2/5",
        "step-3",
        "tts",
        "tts-1",
        "12.5s",
        "$0.00",
        "4.5s",
    ):
        assert value in response.text
    assert 'href="/outputs/job-1/result.m4b"' in response.text
    assert "data-output-url" not in response.text
    assert "step-3" in response.text
    assert "tts-1" in response.text
    assert "malformed audio" in response.text


@respx.mock
@pytest.mark.asyncio
async def test_job_detail_renders_unknown_progress_values(client: AsyncClient) -> None:
    payload = _job_payload()
    payload["progress"] = {
        "completed_steps": 0,
        "total_steps": 0,
        "current_step_id": None,
        "current_worker_type": None,
        "current_worker_id": None,
        "eta_seconds": None,
    }
    respx.get(f"{_ORCH_URL}/jobs/job-1").mock(return_value=httpx.Response(200, json=payload))

    response = await client.get("/partials/jobs/job-1")

    assert response.status_code == 200
    assert "Current step" in response.text
    assert "Current worker type" in response.text
    assert "Current worker ID" in response.text
    assert "Unknown" in response.text


@respx.mock
@pytest.mark.asyncio
async def test_output_proxy_streams_orchestrator_artifact(client: AsyncClient) -> None:
    respx.get(f"{_ORCH_URL}/jobs/job-1/outputs/result.m4b").mock(
        return_value=httpx.Response(
            200,
            content=b"audio",
            headers={"content-type": "audio/mp4", "content-disposition": 'attachment; filename="result.m4b"'},
        )
    )

    response = await client.get("/outputs/job-1/result.m4b")

    assert response.status_code == 200
    assert response.content == b"audio"
    assert response.headers["content-type"] == "audio/mp4"


@respx.mock
@pytest.mark.asyncio
async def test_job_detail_shows_unavailable_on_orchestrator_failure(client: AsyncClient) -> None:
    respx.get(f"{_ORCH_URL}/jobs/job-1").mock(side_effect=httpx.ConnectError("refused"))

    response = await client.get("/partials/jobs/job-1")

    assert response.status_code == 200
    assert "Job details unavailable" in response.text
    assert "FAILED" not in response.text
