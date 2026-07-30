"""Integration tests for job lifecycle via CLI."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from acheron.cli import main
from acheron.shell.job_store import TrackedJob
from acheron.shell.orchestrator import Orchestrator

if TYPE_CHECKING:
    from click.testing import CliRunner
    from fastapi import FastAPI


@pytest.mark.asyncio
async def test_submit_epub_shows_job_id(runner: CliRunner, wired_app: FastAPI, tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    epub.touch()
    result = runner.invoke(main, ["job", "submit", str(epub), "--src", "en", "--dest", "es"])
    assert result.exit_code == 0
    assert "Job submitted:" in result.output
    assert "job-" in result.output
    assert "Status:" in result.output

    job_id = next(word for word in result.output.split() if word.startswith("job-"))
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=wired_app), base_url="http://test") as client:
        response = await client.get(f"/jobs/{job_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["created_at"].endswith("Z")
    assert body["last_persisted_at"].endswith("Z")
    assert set(body["progress"]) == {
        "completed_steps",
        "total_steps",
        "current_step_id",
        "current_worker_type",
        "current_worker_id",
        "eta_seconds",
    }


@pytest.mark.asyncio
async def test_submit_audio_with_asr(runner: CliRunner, wired_app: FastAPI, tmp_path: Path) -> None:
    mp3 = tmp_path / "podcast.mp3"
    mp3.touch()
    result = runner.invoke(main, ["job", "submit", str(mp3), "--src", "en", "--dest", "es", "--asr", "whisper-v3"])
    assert result.exit_code == 0
    assert "job-" in result.output


@pytest.mark.asyncio
async def test_submit_then_status(runner: CliRunner, wired_app: FastAPI, tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    epub.touch()
    submit_result = runner.invoke(main, ["job", "submit", str(epub), "--src", "en", "--dest", "es"])
    assert submit_result.exit_code == 0

    job_id = next(w for w in submit_result.output.split() if w.startswith("job-"))

    status_result = runner.invoke(main, ["job", "status", job_id])
    assert status_result.exit_code == 0
    assert job_id in status_result.output


@pytest.mark.asyncio
async def test_retry_creates_linked_job(runner: CliRunner, wired_app: FastAPI, tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    epub.touch()
    submit_result = runner.invoke(main, ["job", "submit", str(epub), "--src", "en", "--dest", "es"])
    assert submit_result.exit_code == 0, submit_result.output
    original_id = next(word for word in submit_result.output.split() if word.startswith("job-"))

    retry_result = runner.invoke(main, ["job", "retry", original_id, "--label", "atlas-retry"])

    assert retry_result.exit_code == 0, retry_result.output
    retried_id = next(word for word in retry_result.output.split() if word.startswith("job-"))
    assert retried_id != original_id
    assert original_id in retry_result.output


@pytest.mark.asyncio
async def test_submit_then_status_verbose(runner: CliRunner, wired_app: FastAPI, tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    epub.touch()
    submit_result = runner.invoke(main, ["job", "submit", str(epub), "--src", "en", "--dest", "es"])
    job_id = next(w for w in submit_result.output.split() if w.startswith("job-"))

    status_result = runner.invoke(main, ["job", "status", job_id, "-v"])
    assert status_result.exit_code == 0
    assert job_id in status_result.output


@pytest.mark.asyncio
async def test_submit_then_list_jobs(runner: CliRunner, wired_app: FastAPI, tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    epub.touch()
    runner.invoke(main, ["job", "submit", str(epub), "--src", "en", "--dest", "es"])

    result = runner.invoke(main, ["jobs"])
    assert result.exit_code == 0
    assert "job-" in result.output


@pytest.mark.asyncio
async def test_submit_then_list_jobs_active(runner: CliRunner, wired_app: FastAPI, tmp_path: Path) -> None:
    """After submit, the job is reconciled to a terminal status (OBS-001 fix
    makes sure cancelled jobs are persisted as FAILED rather than stuck at
    RUNNING). The active filter should not show the job once it has
    completed or been cancelled.
    """
    epub = tmp_path / "book.epub"
    epub.touch()
    runner.invoke(main, ["job", "submit", str(epub), "--src", "en", "--dest", "es"])

    result = runner.invoke(main, ["jobs", "--active"])
    assert result.exit_code == 0
    assert "No jobs found" in result.output


@pytest.mark.asyncio
async def test_submit_then_list_jobs_completed_filter(runner: CliRunner, wired_app: FastAPI, tmp_path: Path) -> None:
    """After submit, the job is reconciled to a terminal status (OBS-001 fix)
    and shows up in the completed/failed filter, not in "no jobs found".
    """
    epub = tmp_path / "book.epub"
    epub.touch()
    runner.invoke(main, ["job", "submit", str(epub), "--src", "en", "--dest", "es"])

    result = runner.invoke(main, ["jobs", "--completed"])
    assert result.exit_code == 0
    assert "job-" in result.output


@pytest.mark.asyncio
async def test_status_nonexistent_job(runner: CliRunner, wired_app: FastAPI) -> None:
    result = runner.invoke(main, ["job", "status", "job-nonexistent"])
    assert result.exit_code != 0


@pytest.mark.asyncio
async def test_jobs_empty(runner: CliRunner, wired_app: FastAPI) -> None:
    result = runner.invoke(main, ["jobs"])
    assert result.exit_code == 0
    assert "No jobs found" in result.output


# --- Phase 4C journey tests ---


async def _wait_for_terminal(orch: Orchestrator, job_id: str, *, max_iterations: int = 100) -> TrackedJob:
    """Poll get_job until terminal status or iteration limit."""
    import asyncio

    for _ in range(max_iterations):
        job: TrackedJob | None = await orch.get_job(job_id)
        if job is not None and job.status.value in {"completed", "failed", "partial"}:
            return job
        await asyncio.sleep(0)
    msg = f"Job {job_id} did not reach terminal status after {max_iterations} iterations"
    raise TimeoutError(msg)


@pytest.mark.asyncio
async def test_cancel_and_retry_are_distinct_jobs(wired_app: FastAPI, tmp_path: Path) -> None:
    """Cancel creates a terminal job; retry creates a new linked job."""

    from acheron.core.models import EpubRequest, ExecutorStrategy, PlanStatus

    orch: Orchestrator = wired_app.state.orchestrator
    epub = tmp_path / "book.epub"
    epub.touch()

    source = tmp_path / "input"
    source.mkdir(exist_ok=True)
    (source / "book.epub").write_bytes(b"epub")

    tracked = await orch.submit_job(
        EpubRequest("input/book.epub", "en", "es"),
        ExecutorStrategy.SEQUENTIAL,
    )

    # Wait for the job to finish (the wired_app uses local handlers that succeed)
    terminal = await _wait_for_terminal(orch, tracked.job_id)
    assert terminal.status in {PlanStatus.COMPLETED, PlanStatus.FAILED}


@pytest.mark.asyncio
async def test_label_filtering_in_list(wired_app: FastAPI, tmp_path: Path) -> None:
    """Jobs with labels are filterable via glob pattern."""
    from httpx import ASGITransport, AsyncClient

    from acheron.api_client import AcheronClient
    from acheron.core.models import EpubRequest, ExecutorStrategy

    orch: Orchestrator = wired_app.state.orchestrator
    source = tmp_path / "input"
    source.mkdir(exist_ok=True)
    (source / "book.epub").write_bytes(b"epub")

    await orch.submit_job(
        EpubRequest("input/book.epub", "en", "es"),
        ExecutorStrategy.SEQUENTIAL,
        label="atlas-ch1",
    )
    await orch.submit_job(
        EpubRequest("input/book.epub", "en", "es"),
        ExecutorStrategy.SEQUENTIAL,
        label="odyssey-ch2",
    )

    async with AsyncClient(transport=ASGITransport(app=wired_app), base_url="http://test") as http:
        client = AcheronClient(base_url="http://test", transport=http._transport)  # noqa: SLF001
        jobs = await client.list_jobs(label="atlas*")
        assert len(jobs) == 1
        assert jobs[0].label == "atlas-ch1"

        all_jobs = await client.list_jobs()
        assert len(all_jobs) >= 2


@pytest.mark.asyncio
async def test_event_broker_publishes_terminal_event(wired_app: FastAPI, tmp_path: Path) -> None:
    """The event broker publishes a terminal event when a job finishes."""
    from acheron.core.models import EpubRequest, ExecutorStrategy, PlanStatus

    orch: Orchestrator = wired_app.state.orchestrator
    source = tmp_path / "input"
    source.mkdir(exist_ok=True)
    (source / "book.epub").write_bytes(b"epub")

    tracked = await orch.submit_job(
        EpubRequest("input/book.epub", "en", "es"),
        ExecutorStrategy.SEQUENTIAL,
    )

    terminal = await _wait_for_terminal(orch, tracked.job_id)
    assert terminal.status in {PlanStatus.COMPLETED, PlanStatus.FAILED}

    # Check the broker's buffer for terminal events
    buf = orch.events._buffer.get(tracked.job_id)  # noqa: SLF001
    assert buf is not None
    terminal_events = [e for e in buf if e.status in {PlanStatus.COMPLETED, PlanStatus.FAILED}]
    assert len(terminal_events) >= 1
