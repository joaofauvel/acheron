"""Tests for allowlisted job output downloads."""

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from acheron.core.models import (
    EpubRequest,
    ExecutorStrategy,
    OutputFile,
    PlanResult,
    PlanStatus,
)
from acheron.shell.api.app import create_app
from acheron.shell.api.routes.job_outputs import _open_output_fd
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

    output_dir = tmp_path / "job-1" / "package"
    output_dir.mkdir(parents=True)
    output_path = output_dir / "result.m4b"
    output_path.write_bytes(b"audio")
    second_output_path = output_dir / "chapter.m4b"
    second_output_path.write_bytes(b"chapter")
    outside_path = tmp_path / "external" / "outside.m4b"
    outside_path.parent.mkdir()
    outside_path.write_bytes(b"secret")
    symlink_target = tmp_path / "external" / "symlink-target.m4b"
    symlink_target.write_bytes(b"secret")
    symlink_path = output_dir / "symlink.m4b"
    symlink_path.symlink_to(symlink_target)
    fifo_path = output_dir / "output.pipe"
    if hasattr(os, "mkfifo"):
        os.mkfifo(fifo_path)
    else:
        fifo_path.write_bytes(b"")
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
                        path=str(output_path),
                        filename="result.m4b",
                        size_bytes=5,
                        checksum="checksum",
                        content_type="audio/mp4",
                    ),
                    OutputFile(
                        path=str(second_output_path),
                        filename="chapter.m4b",
                        size_bytes=7,
                        checksum="checksum",
                        content_type="audio/mp4",
                    ),
                    OutputFile(
                        path=str(outside_path),
                        filename="outside.m4b",
                        size_bytes=6,
                        checksum="checksum",
                        content_type="audio/mp4",
                    ),
                    OutputFile(
                        path=str(symlink_path),
                        filename="symlink.m4b",
                        size_bytes=6,
                        checksum="checksum",
                        content_type="audio/mp4",
                    ),
                    OutputFile(
                        path=str(fifo_path),
                        filename="output.pipe",
                        size_bytes=0,
                        checksum="checksum",
                        content_type="application/octet-stream",
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
    response = await client_with_output.get("/jobs/job-1/outputs/0")

    assert response.status_code == 200
    assert response.content == b"audio"
    assert response.headers["content-type"] == "audio/mp4"


@pytest.mark.asyncio
async def test_output_route_rejects_out_of_range_index(client_with_output: AsyncClient) -> None:
    response = await client_with_output.get("/jobs/job-1/outputs/99")

    assert response.status_code == 404
    assert response.json()["detail"]["type"] == "OutputNotFoundError"


@pytest.mark.asyncio
async def test_output_route_rejects_non_integer_index(client_with_output: AsyncClient) -> None:
    response = await client_with_output.get("/jobs/job-1/outputs/secret.txt")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_output_route_serves_second_listed_artifact(client_with_output: AsyncClient) -> None:
    response = await client_with_output.get("/jobs/job-1/outputs/1")

    assert response.status_code == 200
    assert response.content == b"chapter"


@pytest.mark.asyncio
async def test_output_route_rejects_artifact_outside_job_directory(
    client_with_output: AsyncClient,
) -> None:
    response = await client_with_output.get("/jobs/job-1/outputs/2")

    assert response.status_code == 404
    assert not response.content.startswith(b"secret")


@pytest.mark.asyncio
async def test_output_route_rejects_symlink_outside_job_directory(client_with_output: AsyncClient) -> None:
    response = await client_with_output.get("/jobs/job-1/outputs/3")

    assert response.status_code == 404
    assert not response.content.startswith(b"secret")


@pytest.mark.skipif(
    not all(hasattr(os, name) for name in ("mkfifo", "O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK")),
    reason="FIFO descriptor safety requires POSIX open flags",
)
@pytest.mark.asyncio
async def test_output_route_rejects_fifo_without_blocking(client_with_output: AsyncClient) -> None:
    response = await client_with_output.get("/jobs/job-1/outputs/4")

    assert response.status_code == 404
    assert response.json()["detail"]["type"] == "OutputNotFoundError"


@pytest.mark.asyncio
async def test_output_route_rejects_missing_artifact(client_with_output: AsyncClient) -> None:
    transport = cast("ASGITransport", client_with_output._transport)  # noqa: SLF001
    app = cast("FastAPI", transport.app)
    output_path = app.state.orchestrator.settings.orchestrator.data_dir / "job-1" / "package" / "result.m4b"
    output_path.unlink()

    response = await client_with_output.get("/jobs/job-1/outputs/0")

    assert response.status_code == 404
    assert response.json()["detail"]["type"] == "OutputNotFoundError"


def test_open_output_fd_remains_pinned_after_path_replacement(tmp_path: Path) -> None:
    output = tmp_path / "job-1" / "package" / "result.m4b"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"audio")
    replacement = tmp_path / "secret.m4b"
    replacement.write_bytes(b"secret")

    file_fd, _ = _open_output_fd(tmp_path, "job-1", str(output))
    try:
        output.unlink()
        output.symlink_to(replacement)
        assert os.read(file_fd, 5) == b"audio"
    finally:
        os.close(file_fd)


def test_open_output_fd_rejects_stored_path_outside_job(tmp_path: Path) -> None:
    (tmp_path / "job-1").mkdir()
    outside = tmp_path / "result.m4b"
    outside.write_bytes(b"secret")

    with pytest.raises(HTTPException):
        _open_output_fd(tmp_path, "job-1", str(outside))


def test_open_output_fd_rejects_traversal_stored_path(tmp_path: Path) -> None:
    job_dir = tmp_path / "job-1"
    job_dir.mkdir()
    (tmp_path / "secret.txt").write_bytes(b"secret")

    with pytest.raises(HTTPException):
        _open_output_fd(tmp_path, "job-1", str(job_dir / ".." / "secret.txt"))


def test_open_output_fd_rejects_standalone_traversal_components(tmp_path: Path) -> None:
    with pytest.raises(HTTPException):
        _open_output_fd(tmp_path, "..", str(tmp_path / "result.m4b"))


def test_open_output_fd_rejects_job_root_symlink(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-job-root"
    outside.mkdir()
    output = outside / "result.m4b"
    output.write_bytes(b"secret")
    (tmp_path / "job-1").symlink_to(outside, target_is_directory=True)

    with pytest.raises(HTTPException) as raised:
        _open_output_fd(tmp_path, "job-1", str(output))

    assert raised.value.status_code == 404
    detail = cast("dict[str, object]", raised.value.detail)
    assert detail["type"] == "OutputNotFoundError"


@pytest.mark.asyncio
async def test_output_route_serves_relative_stored_path_with_relative_data_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    data_dir = Path("data")
    output_path = data_dir / "job-relative" / "package" / "result.m4b"
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(b"relative-audio")
    jobs = InMemoryJobStore()
    app = create_app(
        registry=InMemoryWorkerStore(),
        job_store=jobs,
        cache=PlanCache(tmp_path / "cache"),
        data_dir=data_dir,
    )
    await app.state.orchestrator.start()
    (tmp_path / "data" / "working").mkdir()
    monkeypatch.chdir(tmp_path / "data" / "working")
    await jobs.put(
        TrackedJob(
            job_id="job-relative",
            request=EpubRequest("book.epub", "en", "es"),
            strategy=ExecutorStrategy.STREAMING,
            created_at=datetime(2026, 7, 29, tzinfo=UTC),
            last_persisted_at=datetime(2026, 7, 29, tzinfo=UTC),
            progress=JobProgressState(completed_steps=1, total_steps=1),
            status=PlanStatus.COMPLETED,
            result=PlanResult(
                plan_id="plan-relative",
                status=PlanStatus.COMPLETED,
                completed_steps=1,
                total_steps=1,
                outputs=(
                    OutputFile(
                        path=str(output_path),
                        filename=output_path.name,
                        size_bytes=len(b"relative-audio"),
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
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/jobs/job-relative/outputs/0")
        assert response.status_code == 200
        assert response.content == b"relative-audio"
    finally:
        await app.state.orchestrator.shutdown()
        await app.state.orchestrator.close()
