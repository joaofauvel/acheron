"""Tests for allowlisted job output downloads."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from acheron.core.models import (
    EpubRequest,
    ExecutorStrategy,
    OutputFile,
    PlanResult,
    PlanStatus,
)
from acheron.shell.api.app import create_app
from acheron.shell.api.routes.job_outputs import safe_output_path
from acheron.shell.cache import PlanCache
from acheron.shell.job_store import JobProgressState, TrackedJob
from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore


@pytest_asyncio.fixture
async def client_with_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
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

    output_dir = tmp_path / "job-1"
    output_dir.mkdir()
    output_path = output_dir / "result.m4b"
    output_path.write_bytes(b"audio")
    await jobs.put(
        TrackedJob(
            job_id="job-1",
            request=EpubRequest(str(tmp_path / "book.epub"), "en", "es"),
            strategy=ExecutorStrategy.STREAMING,
            created_at=datetime(2026, 7, 29, tzinfo=UTC),
            last_persisted_at=datetime(2026, 7, 29, tzinfo=UTC),
            progress=JobProgressState(completed_steps=1, total_steps=1),
            status=PlanStatus.COMPLETED,
            result=PlanResult(
                plan_id="plan-1",
                status=PlanStatus.COMPLETED,
                completed_steps=1,
                total_steps=1,
                outputs=(
                    OutputFile(
                        path=str(tmp_path / "external" / "result.m4b"),
                        filename="result.m4b",
                        size_bytes=5,
                        checksum="checksum",
                        content_type="audio/mp4",
                    ),
                ),
                total_cost=0.0,
                total_duration_seconds=0.0,
                errors=(),
            ),
        )
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await app.state.orchestrator.shutdown()
    await app.state.orchestrator.close()


@pytest.mark.asyncio
async def test_output_route_serves_listed_artifact(client_with_output: AsyncClient) -> None:
    response = await client_with_output.get("/jobs/job-1/outputs/result.m4b")

    assert response.status_code == 200
    assert response.content == b"audio"
    assert response.headers["content-type"] == "audio/mp4"


@pytest.mark.asyncio
async def test_output_route_rejects_unlisted_filename(client_with_output: AsyncClient) -> None:
    response = await client_with_output.get("/jobs/job-1/outputs/secret.txt")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_output_route_rejects_artifact_outside_job_directory(
    client_with_output: AsyncClient,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside.m4b"
    outside.write_bytes(b"secret")
    response = await client_with_output.get("/jobs/job-1/outputs/outside.m4b")

    assert response.status_code == 404
    assert not response.content.startswith(b"secret")


def test_safe_output_path_rejects_absolute_filename(tmp_path: Path) -> None:
    job_dir = tmp_path / "job-1"
    job_dir.mkdir()
    (job_dir / "result.m4b").write_bytes(b"audio")

    with pytest.raises(HTTPException):
        safe_output_path(tmp_path, "job-1", str(job_dir / "result.m4b"))


def test_safe_output_path_rejects_traversal_filename(tmp_path: Path) -> None:
    job_dir = tmp_path / "job-1"
    job_dir.mkdir()
    (tmp_path / "secret.txt").write_bytes(b"secret")

    with pytest.raises(HTTPException):
        safe_output_path(tmp_path, "job-1", "../secret.txt")


def test_safe_output_path_rejects_cross_job_path(tmp_path: Path) -> None:
    outside = tmp_path / "job-2"
    outside.mkdir()
    (outside / "result.m4b").write_bytes(b"secret")

    with pytest.raises(HTTPException):
        safe_output_path(tmp_path, "job-1", "result.m4b")


def test_safe_output_path_rejects_standalone_traversal_components(tmp_path: Path) -> None:
    with pytest.raises(HTTPException):
        safe_output_path(tmp_path, "..", "result.m4b")
    with pytest.raises(HTTPException):
        safe_output_path(tmp_path, "job-1", "..")


def test_safe_output_path_rejects_job_root_symlink_outside_data_dir(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-job-root"
    outside.mkdir()
    (outside / "result.m4b").write_bytes(b"secret")
    (tmp_path / "job-1").symlink_to(outside, target_is_directory=True)

    with pytest.raises(HTTPException):
        safe_output_path(tmp_path, "job-1", "result.m4b")
