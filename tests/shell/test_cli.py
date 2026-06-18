"""Tests for the Acheron CLI."""

from __future__ import annotations

from pathlib import Path

import httpx
import respx
from click.testing import CliRunner

from acheron.cli import main

_BASE_URL = "http://localhost:8000"


@respx.mock
def test_submit_epub(tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    epub.touch()
    respx.post(f"{_BASE_URL}/jobs").mock(
        return_value=httpx.Response(201, json={"job_id": "job-abc", "status": "running", "plan_id": "plan-1"})
    )
    runner = CliRunner()
    result = runner.invoke(main, ["submit", str(epub), "--src", "en", "--dest", "es"])
    assert result.exit_code == 0
    assert "job-abc" in result.output
    assert "running" in result.output


@respx.mock
def test_submit_audio(tmp_path: Path) -> None:
    mp3 = tmp_path / "podcast.mp3"
    mp3.touch()
    respx.post(f"{_BASE_URL}/jobs").mock(
        return_value=httpx.Response(201, json={"job_id": "job-def", "status": "running"})
    )
    runner = CliRunner()
    result = runner.invoke(main, ["submit", str(mp3), "--src", "en", "--dest", "es", "--asr", "whisper-v3"])
    assert result.exit_code == 0
    assert "job-def" in result.output


@respx.mock
def test_submit_with_type_override(tmp_path: Path) -> None:
    unknown = tmp_path / "input.dat"
    unknown.touch()
    respx.post(f"{_BASE_URL}/jobs").mock(
        return_value=httpx.Response(201, json={"job_id": "job-xyz", "status": "running"})
    )
    runner = CliRunner()
    result = runner.invoke(main, ["submit", str(unknown), "--src", "en", "--dest", "es", "--type", "epub"])
    assert result.exit_code == 0
    assert "job-xyz" in result.output


def test_submit_unknown_type(tmp_path: Path) -> None:
    unknown = tmp_path / "input.dat"
    unknown.touch()
    runner = CliRunner()
    result = runner.invoke(main, ["submit", str(unknown), "--src", "en", "--dest", "es"])
    assert result.exit_code == 1
    assert "Cannot detect source type" in result.output


def test_submit_missing_file() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["submit", "/nonexistent.epub", "--src", "en", "--dest", "es"])
    assert result.exit_code != 0


@respx.mock
def test_status() -> None:
    respx.get(f"{_BASE_URL}/jobs/job-abc").mock(
        return_value=httpx.Response(
            200,
            json={
                "job_id": "job-abc",
                "status": "running",
                "plan_id": "plan-1",
                "completed_steps": 2,
                "total_steps": 5,
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(main, ["status", "job-abc"])
    assert result.exit_code == 0
    assert "job-abc" in result.output
    assert "2/5" in result.output


@respx.mock
def test_status_verbose() -> None:
    respx.get(f"{_BASE_URL}/jobs/job-abc").mock(
        return_value=httpx.Response(
            200,
            json={"job_id": "job-abc", "status": "failed", "errors": ["Worker timeout"]},
        )
    )
    runner = CliRunner()
    result = runner.invoke(main, ["status", "job-abc", "-v"])
    assert result.exit_code == 0
    assert "Worker timeout" in result.output


@respx.mock
def test_status_not_found() -> None:
    respx.get(f"{_BASE_URL}/jobs/nonexistent").mock(return_value=httpx.Response(404, json={"detail": "Job not found"}))
    runner = CliRunner()
    result = runner.invoke(main, ["status", "nonexistent"])
    assert result.exit_code != 0
    assert "404" in result.output
    assert "Job not found" in result.output


@respx.mock
def test_jobs_empty() -> None:
    respx.get(f"{_BASE_URL}/jobs").mock(return_value=httpx.Response(200, json={"jobs": []}))
    runner = CliRunner()
    result = runner.invoke(main, ["jobs"])
    assert result.exit_code == 0
    assert "No jobs found" in result.output


@respx.mock
def test_jobs_list() -> None:
    respx.get(f"{_BASE_URL}/jobs").mock(
        return_value=httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "job_id": "job-1",
                        "status": "running",
                        "plan_id": "plan-1",
                        "completed_steps": 1,
                        "total_steps": 3,
                    },
                    {
                        "job_id": "job-2",
                        "status": "completed",
                        "plan_id": "plan-2",
                        "completed_steps": 3,
                        "total_steps": 3,
                    },
                ]
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(main, ["jobs"])
    assert result.exit_code == 0
    assert "job-1" in result.output
    assert "job-2" in result.output


@respx.mock
def test_jobs_filter_active() -> None:
    respx.get(f"{_BASE_URL}/jobs").mock(
        return_value=httpx.Response(
            200,
            json={
                "jobs": [
                    {"job_id": "job-1", "status": "running", "completed_steps": 0, "total_steps": 0},
                    {"job_id": "job-2", "status": "completed", "completed_steps": 3, "total_steps": 3},
                ]
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(main, ["jobs", "--active"])
    assert result.exit_code == 0
    assert "job-1" in result.output
    assert "job-2" not in result.output


@respx.mock
def test_jobs_filter_completed() -> None:
    respx.get(f"{_BASE_URL}/jobs").mock(
        return_value=httpx.Response(
            200,
            json={
                "jobs": [
                    {"job_id": "job-1", "status": "running", "completed_steps": 0, "total_steps": 0},
                    {"job_id": "job-2", "status": "completed", "completed_steps": 3, "total_steps": 3},
                ]
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(main, ["jobs", "--completed"])
    assert result.exit_code == 0
    assert "job-1" not in result.output
    assert "job-2" in result.output


@respx.mock
def test_workers_empty() -> None:
    respx.get(f"{_BASE_URL}/workers").mock(return_value=httpx.Response(200, json={"workers": []}))
    runner = CliRunner()
    result = runner.invoke(main, ["workers"])
    assert result.exit_code == 0
    assert "No workers registered" in result.output


@respx.mock
def test_workers_list() -> None:
    respx.get(f"{_BASE_URL}/workers").mock(
        return_value=httpx.Response(
            200,
            json={
                "workers": [
                    {
                        "worker_id": "tts-1",
                        "endpoint": "http://tts:8000",
                        "transport": "http",
                        "worker_type": "tts",
                        "consecutive_failures": 0,
                    }
                ]
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(main, ["workers"])
    assert result.exit_code == 0
    assert "tts-1" in result.output
    assert "http://tts:8000" in result.output


@respx.mock
def test_capabilities_empty() -> None:
    respx.get(f"{_BASE_URL}/capabilities").mock(return_value=httpx.Response(200, json={"language_pairs": []}))
    runner = CliRunner()
    result = runner.invoke(main, ["capabilities"])
    assert result.exit_code == 0
    assert "No language pairs" in result.output


@respx.mock
def test_capabilities_list() -> None:
    respx.get(f"{_BASE_URL}/capabilities").mock(
        return_value=httpx.Response(
            200,
            json={
                "language_pairs": [
                    {"src": "en", "dst": "es", "workers": ["tts-1", "trans-1"]},
                    {"src": "en", "dst": "fr", "workers": ["tts-2"]},
                ]
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(main, ["capabilities"])
    assert result.exit_code == 0
    assert "en" in result.output
    assert "es" in result.output


@respx.mock
def test_capabilities_filter_src() -> None:
    respx.get(f"{_BASE_URL}/capabilities", params={"src": "en"}).mock(
        return_value=httpx.Response(
            200,
            json={"language_pairs": [{"src": "en", "dst": "es", "workers": ["tts-1"]}]},
        )
    )
    runner = CliRunner()
    result = runner.invoke(main, ["capabilities", "--src", "en"])
    assert result.exit_code == 0
    assert "en" in result.output


@respx.mock
def test_capabilities_filter_dest() -> None:
    respx.get(f"{_BASE_URL}/capabilities", params={"dest": "es"}).mock(
        return_value=httpx.Response(
            200,
            json={"language_pairs": [{"src": "en", "dst": "es", "workers": ["tts-1"]}]},
        )
    )
    runner = CliRunner()
    result = runner.invoke(main, ["capabilities", "--dest", "es"])
    assert result.exit_code == 0
    assert "es" in result.output


@respx.mock
def test_submit_server_error_shows_friendly_message(tmp_path: Path) -> None:
    respx.post(f"{_BASE_URL}/jobs").mock(return_value=httpx.Response(500, json={"detail": "Internal server error"}))
    epub = tmp_path / "book.epub"
    epub.touch()
    runner = CliRunner()
    result = runner.invoke(main, ["submit", str(epub), "--src", "en", "--dest", "es"])
    assert result.exit_code != 0
    assert "500" in result.output
    assert "Internal server error" in result.output


@respx.mock
def test_jobs_server_error_shows_friendly_message() -> None:
    respx.get(f"{_BASE_URL}/jobs").mock(return_value=httpx.Response(503, json={"detail": "Service unavailable"}))
    runner = CliRunner()
    result = runner.invoke(main, ["jobs"])
    assert result.exit_code != 0
    assert "503" in result.output
    assert "Service unavailable" in result.output


@respx.mock
def test_workers_server_error_shows_friendly_message() -> None:
    respx.get(f"{_BASE_URL}/workers").mock(return_value=httpx.Response(500, json={"detail": "Registry failure"}))
    runner = CliRunner()
    result = runner.invoke(main, ["workers"])
    assert result.exit_code != 0
    assert "500" in result.output
    assert "Registry failure" in result.output


@respx.mock
def test_submit_validation_error_shows_detail(tmp_path: Path) -> None:
    respx.post(f"{_BASE_URL}/jobs").mock(
        return_value=httpx.Response(422, json={"detail": "Invalid language path: en→xx"})
    )
    epub = tmp_path / "book.epub"
    epub.touch()
    runner = CliRunner()
    result = runner.invoke(main, ["submit", str(epub), "--src", "en", "--dest", "xx"])
    assert result.exit_code != 0
    assert "422" in result.output
    assert "Invalid language path" in result.output


@respx.mock
def test_connect_error_shows_friendly_message() -> None:
    respx.get(f"{_BASE_URL}/jobs").mock(side_effect=httpx.ConnectError("Connection refused"))
    runner = CliRunner()
    result = runner.invoke(main, ["jobs"])
    assert result.exit_code != 0
    assert "Cannot connect" in result.output
    assert "server running" in result.output.lower()
