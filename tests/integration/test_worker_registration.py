"""Integration tests for worker registration and discovery via CLI."""

from __future__ import annotations

from typing import TYPE_CHECKING

from acheron.cli import main
from acheron.core.models import WorkerCapabilities, WorkerType
from acheron.shell.api.app import create_app
from acheron.shell.cache import PlanCache
from acheron.shell.stores.memory import InMemoryWorkerStore

if TYPE_CHECKING:
    from pathlib import Path

    import pytest
    from click.testing import CliRunner
    from fastapi import FastAPI


def test_workers_shows_registered(runner: CliRunner, wired_app: FastAPI) -> None:
    result = runner.invoke(main, ["workers"])
    assert result.exit_code == 0
    assert "tts-1" in result.output
    assert "trans-1" in result.output


def test_capabilities_shows_language_pairs(runner: CliRunner, wired_app: FastAPI) -> None:
    result = runner.invoke(main, ["capabilities"])
    assert result.exit_code == 0
    assert "en" in result.output
    assert "es" in result.output


def test_capabilities_filter_by_src(runner: CliRunner, wired_app: FastAPI) -> None:
    result = runner.invoke(main, ["capabilities", "--src", "en"])
    assert result.exit_code == 0
    assert "en" in result.output


def test_capabilities_filter_by_dest(runner: CliRunner, wired_app: FastAPI) -> None:
    result = runner.invoke(main, ["capabilities", "--dest", "es"])
    assert result.exit_code == 0
    assert "es" in result.output


def test_capabilities_filter_no_match(runner: CliRunner, wired_app: FastAPI) -> None:
    result = runner.invoke(main, ["capabilities", "--src", "xx"])
    assert result.exit_code == 0
    assert "No language pairs" in result.output


def test_workers_shows_built_in_orchestration_workers(
    tmp_path: Path, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(registry=InMemoryWorkerStore(), cache=PlanCache(tmp_path), data_dir=tmp_path)
    from httpx import ASGITransport

    from acheron.api_client import AcheronClient

    transport = ASGITransport(app=app)
    client = AcheronClient(base_url="http://test", transport=transport)
    monkeypatch.setattr("acheron.cli._get_client", lambda: client)

    result = runner.invoke(main, ["workers"])
    assert result.exit_code == 0
    assert "extraction-local" in result.output
    assert "chunking-local" in result.output
    assert "packaging-local" in result.output


def test_capabilities_empty(tmp_path: Path, runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app(registry=InMemoryWorkerStore(), cache=PlanCache(tmp_path), data_dir=tmp_path)
    from httpx import ASGITransport

    from acheron.api_client import AcheronClient

    transport = ASGITransport(app=app)
    client = AcheronClient(base_url="http://test", transport=transport)
    monkeypatch.setattr("acheron.cli._get_client", lambda: client)

    result = runner.invoke(main, ["capabilities"])
    assert result.exit_code == 0
    assert "No language pairs" in result.output


def test_submit_no_workers_fails_at_plan(tmp_path: Path, runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app(registry=InMemoryWorkerStore(), cache=PlanCache(tmp_path), data_dir=tmp_path)
    from httpx import ASGITransport

    from acheron.api_client import AcheronClient

    transport = ASGITransport(app=app)
    client = AcheronClient(base_url="http://test", transport=transport)
    monkeypatch.setattr("acheron.cli._get_client", lambda: client)

    epub = tmp_path / "book.epub"
    epub.touch()
    result = runner.invoke(main, ["submit", str(epub), "--src", "en", "--dest", "es"])
    assert result.exit_code != 0


def test_submit_wrong_language_fails(tmp_path: Path, runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    reg = InMemoryWorkerStore()
    reg.register(
        "tts-es",
        "http://tts",
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
    app = create_app(registry=reg, cache=PlanCache(tmp_path), data_dir=tmp_path)
    from httpx import ASGITransport

    from acheron.api_client import AcheronClient

    transport = ASGITransport(app=app)
    client = AcheronClient(base_url="http://test", transport=transport)
    monkeypatch.setattr("acheron.cli._get_client", lambda: client)

    epub = tmp_path / "book.epub"
    epub.touch()
    result = runner.invoke(main, ["submit", str(epub), "--src", "en", "--dest", "fr"])
    assert result.exit_code != 0
