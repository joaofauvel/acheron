"""Integration tests for multiple concurrent jobs."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from acheron.cli import main

if TYPE_CHECKING:
    from pathlib import Path

    from click.testing import CliRunner
    from fastapi import FastAPI


@pytest.mark.asyncio
async def test_multiple_submissions_appear_in_list(runner: CliRunner, wired_app: FastAPI, tmp_path: Path) -> None:
    for name in ("a.epub", "b.epub", "c.epub"):
        (tmp_path / name).touch()

    for name in ("a.epub", "b.epub", "c.epub"):
        result = runner.invoke(main, ["job", "submit", str(tmp_path / name), "--src", "en", "--dest", "es"])
        assert result.exit_code == 0

    result = runner.invoke(main, ["jobs"])
    assert result.exit_code == 0
    assert result.output.count("job-") == 3


@pytest.mark.asyncio
async def test_multiple_submissions_get_unique_ids(runner: CliRunner, wired_app: FastAPI, tmp_path: Path) -> None:
    for name in ("a.epub", "b.epub"):
        (tmp_path / name).touch()

    ids = set()
    for name in ("a.epub", "b.epub"):
        result = runner.invoke(main, ["job", "submit", str(tmp_path / name), "--src", "en", "--dest", "es"])
        assert result.exit_code == 0
        job_id = next(w for w in result.output.split() if w.startswith("job-"))
        ids.add(job_id)

    assert len(ids) == 2


@pytest.mark.asyncio
async def test_active_filter_shows_running_jobs(runner: CliRunner, wired_app: FastAPI, tmp_path: Path) -> None:
    for name in ("a.epub", "b.epub"):
        (tmp_path / name).touch()

    runner.invoke(main, ["job", "submit", str(tmp_path / "a.epub"), "--src", "en", "--dest", "es"])
    runner.invoke(main, ["job", "submit", str(tmp_path / "b.epub"), "--src", "en", "--dest", "es"])

    result = runner.invoke(main, ["jobs", "--active"])
    assert result.exit_code == 0
    assert "job-" in result.output


@pytest.mark.asyncio
async def test_list_jobs_after_submission(runner: CliRunner, wired_app: FastAPI, tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    epub.touch()
    runner.invoke(main, ["job", "submit", str(epub), "--src", "en", "--dest", "es"])

    result = runner.invoke(main, ["jobs"])
    assert result.exit_code == 0
    assert "job-" in result.output
