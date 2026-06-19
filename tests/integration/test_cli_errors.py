"""Integration tests for CLI error handling."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from acheron.cli import main

if TYPE_CHECKING:
    from pathlib import Path

    from click.testing import CliRunner
    from fastapi import FastAPI


@pytest.mark.asyncio
async def test_submit_missing_file(runner: CliRunner, wired_app: FastAPI) -> None:
    result = runner.invoke(main, ["submit", "/nonexistent.epub", "--src", "en", "--dest", "es"])
    assert result.exit_code != 0


@pytest.mark.asyncio
async def test_submit_unknown_extension_no_type(runner: CliRunner, wired_app: FastAPI, tmp_path: Path) -> None:
    unknown = tmp_path / "input.dat"
    unknown.touch()
    result = runner.invoke(main, ["submit", str(unknown), "--src", "en", "--dest", "es"])
    assert result.exit_code == 1
    assert "Cannot detect source type" in result.output


@pytest.mark.asyncio
async def test_submit_invalid_source_type(runner: CliRunner, wired_app: FastAPI, tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    epub.touch()
    result = runner.invoke(main, ["submit", str(epub), "--src", "en", "--dest", "es", "--type", "pdf"])
    assert result.exit_code != 0


@pytest.mark.asyncio
async def test_submit_invalid_executor_strategy(runner: CliRunner, wired_app: FastAPI, tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    epub.touch()
    result = runner.invoke(main, ["submit", str(epub), "--src", "en", "--dest", "es", "--executor", "invalid"])
    assert result.exit_code != 0


@pytest.mark.asyncio
async def test_status_nonexistent(runner: CliRunner, wired_app: FastAPI) -> None:
    result = runner.invoke(main, ["status", "job-doesnotexist"])
    assert result.exit_code != 0
    assert "404" in result.output


@pytest.mark.asyncio
async def test_submit_missing_required_options(runner: CliRunner, wired_app: FastAPI, tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    epub.touch()
    result = runner.invoke(main, ["submit", str(epub)])
    assert result.exit_code != 0


@pytest.mark.asyncio
async def test_submit_invalid_language_pair(runner: CliRunner, wired_app: FastAPI, tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    epub.touch()
    result = runner.invoke(main, ["submit", str(epub), "--src", "xx", "--dest", "yy"])
    assert result.exit_code != 0


@pytest.mark.asyncio
async def test_help_shows_commands(runner: CliRunner, wired_app: FastAPI) -> None:
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "submit" in result.output
    assert "status" in result.output
    assert "workers" in result.output
    assert "capabilities" in result.output


@pytest.mark.asyncio
async def test_submit_help(runner: CliRunner, wired_app: FastAPI) -> None:
    result = runner.invoke(main, ["submit", "--help"])
    assert result.exit_code == 0
    assert "--src" in result.output
    assert "--dest" in result.output
    assert "--type" in result.output


@pytest.mark.asyncio
async def test_type_override_epub(runner: CliRunner, wired_app: FastAPI, tmp_path: Path) -> None:
    dat = tmp_path / "mybook"
    dat.write_bytes(b"fake epub content")
    result = runner.invoke(main, ["submit", str(dat), "--src", "en", "--dest", "es", "--type", "epub"])
    assert result.exit_code == 0
    assert "job-" in result.output
