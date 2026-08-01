"""Integration tests for worker registration and discovery via CLI."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient

from acheron.cli import main
from acheron.core.models import WorkerCapabilities, WorkerStatus, WorkerType
from acheron.shell.api.app import create_app
from acheron.shell.cache import PlanCache
from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore

if TYPE_CHECKING:
    from pathlib import Path

    from click.testing import CliRunner
    from fastapi import FastAPI


@pytest.mark.asyncio
async def test_registration_and_discovery_expose_only_safe_projections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ACHERON_REGISTRATION_TOKEN", "test-registration-token-must-be-32-chars-or-more")
    registry = InMemoryWorkerStore()
    app = create_app(
        registry=registry,
        job_store=InMemoryJobStore(),
        cache=PlanCache(tmp_path),
        data_dir=tmp_path,
    )
    await app.state.orchestrator.start()
    token = "Bearer test-registration-token-must-be-32-chars-or-more"
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            registration = await client.post(
                "/workers",
                headers={"Authorization": token},
                json={
                    "worker_id": "tts-redaction",
                    "endpoint": "https://private.example:8443",
                    "transport": "http",
                    "capabilities": {
                        "worker_type": "tts",
                        "supported_languages_in": ["en"],
                        "supported_languages_out": ["en"],
                        "metadata": {
                            "health_provider": "aws",
                            "speakers": ["Ryan", "https://provider.example/token=secret", "token=secret"],
                        },
                    },
                },
            )
            assert registration.status_code == 201
            assert registration.json() == {"worker_id": "tts-redaction", "status": "healthy"}

            registry = app.state.orchestrator._registry  # noqa: SLF001
            await registry.set_worker_status(
                "tts-redaction",
                WorkerStatus.OFFLINE,
                """provider aws error: request body: {"token":"secret"}
Traceback
ValueError: leaked""",
            )
            anonymous_workers = await client.get("/workers")
            token_workers = await client.get("/workers", headers={"Authorization": token})
            assert anonymous_workers.status_code == token_workers.status_code == 200
            assert anonymous_workers.json() == token_workers.json()
            body = str(anonymous_workers.json())
            assert "private.example" not in body
            assert "secret" not in body
            assert "Traceback" not in body
            assert "provider aws" not in body

            anonymous_caps = await client.get("/capabilities", params={"type": "tts"})
            token_caps = await client.get("/capabilities", params={"type": "tts"}, headers={"Authorization": token})
            assert anonymous_caps.json() == token_caps.json()
            caps_body = str(anonymous_caps.json())
            assert "private.example" not in caps_body
            assert "secret" not in caps_body
            assert "https://" not in caps_body
            assert "speakers" in caps_body
            assert "Ryan" in caps_body
    finally:
        await app.state.orchestrator.shutdown()
        await app.state.orchestrator.close()


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
    assert result.exit_code != 0
    assert "Error 422" in result.output
    assert "source language is not supported" in result.output
    assert "'xx'" not in result.output
    assert "sources: de, en, es, fr" in result.output


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
