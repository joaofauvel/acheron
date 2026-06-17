"""Integration tests for job lifecycle via CLI."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from acheron.cli import main

if TYPE_CHECKING:
    from click.testing import CliRunner
    from fastapi import FastAPI


def test_submit_epub_shows_job_id(runner: CliRunner, wired_app: FastAPI, tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    epub.touch()
    result = runner.invoke(main, ["submit", str(epub), "--src", "en", "--dest", "es"])
    assert result.exit_code == 0
    assert "Job submitted:" in result.output
    assert "job-" in result.output
    assert "Status:" in result.output


def test_submit_audio_with_asr(runner: CliRunner, wired_app: FastAPI, tmp_path: Path) -> None:
    mp3 = tmp_path / "podcast.mp3"
    mp3.touch()
    result = runner.invoke(main, ["submit", str(mp3), "--src", "en", "--dest", "es", "--asr", "whisper-v3"])
    assert result.exit_code == 0
    assert "job-" in result.output


def test_submit_then_status(runner: CliRunner, wired_app: FastAPI, tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    epub.touch()
    submit_result = runner.invoke(main, ["submit", str(epub), "--src", "en", "--dest", "es"])
    assert submit_result.exit_code == 0

    job_id = next(w for w in submit_result.output.split() if w.startswith("job-"))

    status_result = runner.invoke(main, ["status", job_id])
    assert status_result.exit_code == 0
    assert job_id in status_result.output


def test_submit_then_status_verbose(runner: CliRunner, wired_app: FastAPI, tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    epub.touch()
    submit_result = runner.invoke(main, ["submit", str(epub), "--src", "en", "--dest", "es"])
    job_id = next(w for w in submit_result.output.split() if w.startswith("job-"))

    status_result = runner.invoke(main, ["status", job_id, "-v"])
    assert status_result.exit_code == 0
    assert job_id in status_result.output


def test_submit_then_list_jobs(runner: CliRunner, wired_app: FastAPI, tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    epub.touch()
    runner.invoke(main, ["submit", str(epub), "--src", "en", "--dest", "es"])

    result = runner.invoke(main, ["jobs"])
    assert result.exit_code == 0
    assert "job-" in result.output


def test_submit_then_list_jobs_active(runner: CliRunner, wired_app: FastAPI, tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    epub.touch()
    runner.invoke(main, ["submit", str(epub), "--src", "en", "--dest", "es"])

    result = runner.invoke(main, ["jobs", "--active"])
    assert result.exit_code == 0
    assert "job-" in result.output


def test_submit_then_list_jobs_completed_filter(runner: CliRunner, wired_app: FastAPI, tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    epub.touch()
    runner.invoke(main, ["submit", str(epub), "--src", "en", "--dest", "es"])

    result = runner.invoke(main, ["jobs", "--completed"])
    assert result.exit_code == 0
    assert "No jobs found" in result.output


def test_status_nonexistent_job(runner: CliRunner, wired_app: FastAPI) -> None:
    result = runner.invoke(main, ["status", "job-nonexistent"])
    assert result.exit_code != 0


def test_jobs_empty(runner: CliRunner, wired_app: FastAPI) -> None:
    result = runner.invoke(main, ["jobs"])
    assert result.exit_code == 0
    assert "No jobs found" in result.output
