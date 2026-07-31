"""Tests for cost explanation and summary routes."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from acheron.core.models import (
    CostBasis,
    CostBreakdown,
    CostEstimate,
    EpubRequest,
    ExecutorStrategy,
    PlanResult,
    PlanStatus,
    WorkerType,
)
from acheron.shell.api.app import create_app
from acheron.shell.cache import PlanCache
from acheron.shell.job_store import TrackedJob
from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore


@pytest.mark.asyncio
async def test_get_job_cost_exposes_gpu_and_cache_age(tmp_path: Path) -> None:
    jobs = InMemoryJobStore()
    app = create_app(
        registry=InMemoryWorkerStore(),
        job_store=jobs,
        cache=PlanCache(tmp_path),
        data_dir=tmp_path,
    )
    await app.state.orchestrator.start()
    await jobs.put(
        TrackedJob(
            job_id="job-measured",
            request=EpubRequest("input/book.epub", "en", "es"),
            strategy=ExecutorStrategy.STREAMING,
            status=PlanStatus.FAILED,
            result=PlanResult(
                plan_id="plan-1",
                status=PlanStatus.FAILED,
                completed_steps=1,
                total_steps=1,
                outputs=(),
                total_cost=0.34,
                total_duration_seconds=1.0,
                total_cost_basis=CostBasis.MEASURED,
                cost_breakdown=(
                    CostBreakdown(
                        step_id="synthesize",
                        worker_type=WorkerType.TTS,
                        worker_id="tts-1",
                        gpu_seconds=1800.0,
                        estimate=CostEstimate(
                            cost=0.34,
                            basis=CostBasis.MEASURED,
                            rate_per_hour=0.69,
                            gpu_type="L4",
                            secure_cloud=False,
                            queried_at=datetime(2026, 7, 30, 12, tzinfo=UTC),
                            cache_age_seconds=0.0,
                        ),
                    ),
                ),
            ),
        )
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/jobs/job-measured/cost")
        assert response.status_code == 200
        assert response.json()["cost_breakdown"][0]["gpu_type"] == "L4"
        assert response.json()["cost_breakdown"][0]["cache_age_seconds"] == 0.0
    finally:
        await app.state.orchestrator.shutdown()
        await app.state.orchestrator.close()


@pytest.mark.asyncio
async def test_cost_window_is_query_parameter_and_unknown_counted(tmp_path: Path) -> None:
    app = create_app(
        registry=InMemoryWorkerStore(),
        job_store=InMemoryJobStore(),
        cache=PlanCache(tmp_path),
        data_dir=tmp_path,
    )
    await app.state.orchestrator.start()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.request("GET", "/cost", json={"window": "all"})
        assert response.status_code == 200
        assert response.json()["window"] == "7d"
        assert response.json()["unknown_cost_jobs"] == 0
    finally:
        await app.state.orchestrator.shutdown()
        await app.state.orchestrator.close()
