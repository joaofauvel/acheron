"""Tests for job API routes."""

import asyncio
from pathlib import Path

import pytest


class TestJobRoutes:
    @pytest.mark.asyncio
    async def test_get_job_maps_total_cost_basis(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from httpx import ASGITransport, AsyncClient

        from acheron.core.models import CostBasis, EpubRequest, ExecutorStrategy, PlanResult, PlanStatus
        from acheron.shell.api.app import create_app
        from acheron.shell.cache import PlanCache
        from acheron.shell.job_store import TrackedJob
        from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore

        monkeypatch.delenv("ACHERON_REGISTRATION_TOKEN", raising=False)
        monkeypatch.setenv("ACHERON_OPEN_REGISTRATION", "1")
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
                request=EpubRequest(source_path="/input/book.epub", source_language="en", target_language="es"),
                strategy=ExecutorStrategy.SEQUENTIAL,
                status=PlanStatus.COMPLETED,
                result=PlanResult(
                    plan_id="plan-measured",
                    status=PlanStatus.COMPLETED,
                    completed_steps=1,
                    total_steps=1,
                    outputs=(),
                    total_cost=0.25,
                    total_duration_seconds=1.0,
                    total_cost_basis=CostBasis.MEASURED,
                ),
            )
        )
        transport = ASGITransport(app=app)
        try:
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                response = await c.get("/jobs/job-measured")
        finally:
            await app.state.orchestrator.shutdown()
            await app.state.orchestrator.close()
        assert response.status_code == 200
        assert response.json()["total_cost_basis"] == "measured"

    @pytest.mark.asyncio
    async def test_submit_job(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.post(
            "/jobs",
            json={
                "source_type": "epub",
                "source_path": "/input/book.epub",
                "source_language": "en",
                "target_language": "es",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["job_id"].startswith("job-")
        assert data["status"] in ("running", "completed")

    @pytest.mark.asyncio
    async def test_submit_job_executes_end_to_end(self, client) -> None:  # type: ignore[no-untyped-def]
        """Submitted job's background _execute task actually runs.

        The ``client`` fixture starts the orchestrator (registers local
        workers) and uses the test's event loop, so background ``_execute``
        tasks survive. Polls until status changes from ``"running"`` to
        prove the background task ran (it would otherwise stay ``"running"``
        forever). Note: the default workers are fake HTTP endpoints, so
        the final status is typically ``"partial"`` — we just need to see
        the state transition.
        """
        response = await client.post(
            "/jobs",
            json={
                "source_type": "epub",
                "source_path": "/input/book.epub",
                "source_language": "en",
                "target_language": "es",
            },
        )
        assert response.status_code == 201
        job_id = response.json()["job_id"]

        loop = asyncio.get_running_loop()
        deadline = loop.time() + 5.0
        status = "running"
        while loop.time() < deadline:
            poll = await client.get(f"/jobs/{job_id}")
            status = poll.json()["status"]
            if status != "running":
                break
            await asyncio.sleep(0.05)
        assert status != "running", "job stayed running: _execute task never ran"

    @pytest.mark.asyncio
    async def test_submit_job_invalid_strategy(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.post(
            "/jobs",
            json={
                "source_type": "epub",
                "source_path": "/input/book.epub",
                "source_language": "en",
                "target_language": "es",
                "executor_strategy": "invalid",
            },
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_submit_job_invalid_source_type(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.post(
            "/jobs",
            json={
                "source_type": "pdf",
                "source_path": "/input/doc.pdf",
                "source_language": "en",
                "target_language": "es",
            },
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_get_job(self, client) -> None:  # type: ignore[no-untyped-def]
        submit = await client.post(
            "/jobs",
            json={
                "source_type": "epub",
                "source_path": "/input/book.epub",
                "source_language": "en",
                "target_language": "es",
            },
        )
        job_id = submit.json()["job_id"]

        response = await client.get(f"/jobs/{job_id}")
        assert response.status_code == 200
        assert response.json()["job_id"] == job_id

    @pytest.mark.asyncio
    async def test_get_job_not_found(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get("/jobs/nonexistent")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_list_jobs(self, client) -> None:  # type: ignore[no-untyped-def]
        await client.post(
            "/jobs",
            json={
                "source_type": "epub",
                "source_path": "/input/book.epub",
                "source_language": "en",
                "target_language": "es",
            },
        )
        response = await client.get("/jobs")
        assert response.status_code == 200
        assert len(response.json()["jobs"]) == 1

    @pytest.mark.asyncio
    async def test_resume_job_route(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.post(
            "/jobs",
            json={
                "source_type": "epub",
                "source_path": "/input/book.epub",
                "source_language": "en",
                "target_language": "es",
            },
        )
        job_id = response.json()["job_id"]

        loop = asyncio.get_running_loop()
        deadline = loop.time() + 5.0
        while loop.time() < deadline:
            status_resp = await client.get(f"/jobs/{job_id}")
            if status_resp.json()["status"] in ("completed", "failed", "partial"):
                break
            await asyncio.sleep(0.05)

        resume_resp = await client.post(f"/jobs/{job_id}/resume")
        assert resume_resp.status_code == 200
        assert resume_resp.json()["status"] == "running"

    @pytest.mark.asyncio
    async def test_submit_job_unsupported_language(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        """Test that submitting a job with unsupported language returns 422."""
        from httpx import ASGITransport, AsyncClient

        from acheron.shell.api.app import create_app
        from acheron.shell.cache import PlanCache
        from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore

        monkeypatch.delenv("ACHERON_REGISTRATION_TOKEN", raising=False)
        monkeypatch.setenv("ACHERON_OPEN_REGISTRATION", "1")
        app = create_app(
            registry=InMemoryWorkerStore(),
            job_store=InMemoryJobStore(),
            cache=PlanCache(tmp_path),
            data_dir=tmp_path,
        )
        await app.state.orchestrator.start()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            response = await c.post(
                "/jobs",
                json={
                    "source_type": "epub",
                    "source_path": "/input/book.epub",
                    "source_language": "en",
                    "target_language": "xx",
                },
            )
            assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_submit_job_rejects_extra_fields(self, client) -> None:  # type: ignore[no-untyped-def]
        """SubmitJobRequest must reject unknown fields so client typos fail loudly."""
        response = await client.post(
            "/jobs",
            json={
                "source_type": "epub",
                "source_path": "/input/book.epub",
                "source_language": "en",
                "target_language": "es",
                "executor_strategi": "streaming",  # typo: missing 'y'
            },
        )
        assert response.status_code == 422
        body = response.json()
        assert any("executor_strategi" in str(d).lower() for d in body.get("detail", []))


class TestJobRouteAuth:
    """SEC-005: mutating job routes require auth when no open-registration flag is set."""

    @pytest.mark.asyncio
    async def test_submit_job_rejected_without_token_when_token_set(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When ACHERON_REGISTRATION_TOKEN is set and open_registration is not, POST /jobs returns 401."""
        from httpx import ASGITransport, AsyncClient

        from acheron.shell.api.app import create_app
        from acheron.shell.cache import PlanCache
        from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore

        monkeypatch.setenv("ACHERON_REGISTRATION_TOKEN", "x" * 64)
        monkeypatch.delenv("ACHERON_OPEN_REGISTRATION", raising=False)
        app = create_app(
            registry=InMemoryWorkerStore(),
            job_store=InMemoryJobStore(),
            cache=PlanCache(tmp_path),
            data_dir=tmp_path,
        )
        await app.state.orchestrator.start()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            response = await c.post(
                "/jobs",
                json={
                    "source_type": "epub",
                    "source_path": "/input/book.epub",
                    "source_language": "en",
                    "target_language": "es",
                },
            )
        await app.state.orchestrator.shutdown()
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_resume_job_rejected_without_token_when_token_set(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When ACHERON_REGISTRATION_TOKEN is set, POST /jobs/{id}/resume returns 401."""
        from httpx import ASGITransport, AsyncClient

        from acheron.shell.api.app import create_app
        from acheron.shell.cache import PlanCache
        from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore

        monkeypatch.setenv("ACHERON_REGISTRATION_TOKEN", "x" * 64)
        monkeypatch.delenv("ACHERON_OPEN_REGISTRATION", raising=False)
        app = create_app(
            registry=InMemoryWorkerStore(),
            job_store=InMemoryJobStore(),
            cache=PlanCache(tmp_path),
            data_dir=tmp_path,
        )
        await app.state.orchestrator.start()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            response = await c.post("/jobs/j-1/resume")
        await app.state.orchestrator.shutdown()
        assert response.status_code == 401
