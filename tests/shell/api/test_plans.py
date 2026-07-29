"""Tests for the plans API routes."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient

from acheron.core.models import ExecutorStrategy, Plan, PlanStep, StepStatus, WorkerType
from acheron.shell.api.app import create_app
from acheron.shell.cache import PlanCache
from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore

if TYPE_CHECKING:
    from fastapi import FastAPI


def _sample_plan(plan_id: str = "plan-1") -> Plan:
    return Plan(
        plan_id=plan_id,
        job_id="job-1",
        source_type="epub",
        source_language="en",
        target_language="es",
        executor_strategy=ExecutorStrategy.STREAMING,
        steps=(
            PlanStep(
                step_id="extract",
                type=WorkerType.EXTRACTION,
                depends_on=(),
                status=StepStatus.PENDING,
                payload={"source_path": "/input/book.epub"},
            ),
        ),
    )


async def _make_client(tmp_path: Path) -> tuple[FastAPI, AsyncClient]:
    """Build a minimal app for persisted-plan lookup tests."""
    app = create_app(
        registry=InMemoryWorkerStore(),
        job_store=InMemoryJobStore(),
        cache=PlanCache(tmp_path),
        data_dir=tmp_path,
    )
    await app.state.orchestrator.start()
    transport = ASGITransport(app=app)
    return app, AsyncClient(transport=transport, base_url="http://test")


class TestGetPlanRoute:
    @pytest.mark.asyncio
    async def test_get_plan_returns_public_structure(self, tmp_path: Path) -> None:
        PlanCache(tmp_path).save_plan(_sample_plan("plan-1"))

        app, client = await _make_client(tmp_path)
        try:
            response = await client.get("/plans/plan-1")
        finally:
            await app.state.orchestrator.shutdown()
            await app.state.orchestrator.close()
            await client.aclose()

        assert response.status_code == 200
        body = response.json()
        assert body["plan_id"] == "plan-1"
        assert body["steps"][0]["worker_type"] == "extraction"
        assert "payload" not in body["steps"][0]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("plan_id", ["missing", "../escaped-plan", "plan-../escape"])
    async def test_get_plan_returns_not_found_for_invalid_or_missing_id(
        self,
        tmp_path: Path,
        plan_id: str,
    ) -> None:
        app, client = await _make_client(tmp_path)
        try:
            response = await client.get(f"/plans/{plan_id}")
        finally:
            await app.state.orchestrator.shutdown()
            await app.state.orchestrator.close()
            await client.aclose()

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_plan_corrupted_cache_returns_500_without_leaking_details(
        self,
        tmp_path: Path,
    ) -> None:
        """CacheCorruptedError must surface as 500 with a generic message, not raw file paths."""
        plan_dir = tmp_path / "plan-deadbeef"
        plan_dir.mkdir()
        (plan_dir / "plan.json").write_text("this is not valid json")

        app, client = await _make_client(tmp_path)
        try:
            response = await client.get("/plans/plan-deadbeef")
        finally:
            await app.state.orchestrator.shutdown()
            await app.state.orchestrator.close()
            await client.aclose()

        assert response.status_code == 500
        detail = response.json()["detail"]
        assert "Corrupted" not in detail
        assert "plan.json" not in detail
        assert str(plan_dir) not in detail
