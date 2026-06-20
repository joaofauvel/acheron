"""Integration tests for worker registration and discovery via CLI."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from acheron.cli import main
from acheron.core.models import WorkerCapabilities, WorkerType
from acheron.shell.api.app import create_app
from acheron.shell.cache import PlanCache
from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore

if TYPE_CHECKING:
    from pathlib import Path

    from click.testing import CliRunner
    from fastapi import FastAPI


@pytest.mark.asyncio
async def test_workers_shows_registered(runner: CliRunner, wired_app: FastAPI) -> None:
    result = runner.invoke(main, ["workers"])
    assert result.exit_code == 0
    assert "tts-1" in result.output
    assert "trans-1" in result.output


@pytest.mark.asyncio
async def test_capabilities_shows_language_pairs(runner: CliRunner, wired_app: FastAPI) -> None:
    result = runner.invoke(main, ["capabilities"])
    assert result.exit_code == 0
    assert "en" in result.output
    assert "es" in result.output


@pytest.mark.asyncio
async def test_capabilities_filter_by_src(runner: CliRunner, wired_app: FastAPI) -> None:
    result = runner.invoke(main, ["capabilities", "--src", "en"])
    assert result.exit_code == 0
    assert "en" in result.output


@pytest.mark.asyncio
async def test_capabilities_filter_by_dest(runner: CliRunner, wired_app: FastAPI) -> None:
    result = runner.invoke(main, ["capabilities", "--dest", "es"])
    assert result.exit_code == 0
    assert "es" in result.output


@pytest.mark.asyncio
async def test_capabilities_filter_no_match(runner: CliRunner, wired_app: FastAPI) -> None:
    result = runner.invoke(main, ["capabilities", "--src", "xx"])
    assert result.exit_code == 0
    assert "No language pairs" in result.output


def _wire_app(
    tmp_path: Path, runner: CliRunner, monkeypatch: pytest.MonkeyPatch, *, reg: InMemoryWorkerStore | None = None
) -> None:
    """Build a fresh app, register local workers, and wire the CLI to it.

    Sync helper that drives the async orchestrator startup via ``asyncio.run``
    so tests that use ``CliRunner.invoke`` (sync) can still verify behaviour
    that depends on lifespan-triggered state.
    """
    if reg is None:
        reg = InMemoryWorkerStore()
    app = create_app(
        registry=reg,
        job_store=InMemoryJobStore(),
        cache=PlanCache(tmp_path),
        data_dir=tmp_path,
    )
    asyncio.run(app.state.orchestrator.start())
    from httpx import ASGITransport

    from acheron.api_client import AcheronClient

    transport = ASGITransport(app=app)
    client = AcheronClient(base_url="http://test", transport=transport)
    monkeypatch.setattr("acheron.cli._get_client", lambda: client)


def test_workers_shows_built_in_orchestration_workers(
    tmp_path: Path, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_app(tmp_path, runner, monkeypatch)
    result = runner.invoke(main, ["workers"])
    assert result.exit_code == 0
    assert "extraction-local" in result.output
    assert "chunking-local" in result.output
    assert "packaging-local" in result.output


def test_capabilities_empty(tmp_path: Path, runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    _wire_app(tmp_path, runner, monkeypatch)
    result = runner.invoke(main, ["capabilities"])
    assert result.exit_code == 0
    assert "No language pairs" in result.output


def test_submit_no_workers_fails_at_plan(tmp_path: Path, runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    _wire_app(tmp_path, runner, monkeypatch)
    epub = tmp_path / "book.epub"
    epub.touch()
    result = runner.invoke(main, ["submit", str(epub), "--src", "en", "--dest", "es"])
    assert result.exit_code != 0


def test_submit_wrong_language_fails(tmp_path: Path, runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    reg = InMemoryWorkerStore()
    asyncio.run(
        reg.register(
            "tts-es",
            "http://127.0.0.1:1",
            "http",
            WorkerCapabilities(
                worker_type=WorkerType.TTS,
                supported_languages_in=frozenset({"es"}),
                supported_languages_out=frozenset({"es"}),
                supported_formats_in=frozenset({"text"}),
                supported_formats_out=frozenset({"wav"}),
                max_payload_bytes=None,
                batch_capable=True,
                model_source=None,
            ),
        )
    )
    _wire_app(tmp_path, runner, monkeypatch, reg=reg)
    epub = tmp_path / "book.epub"
    epub.touch()
    result = runner.invoke(main, ["submit", str(epub), "--src", "en", "--dest", "fr"])
    assert result.exit_code != 0
