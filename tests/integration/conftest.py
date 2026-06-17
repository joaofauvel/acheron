"""Shared fixtures for integration tests.

Integration tests exercise the CLI against a real FastAPI app via ASGI transport,
verifying the full request→route→orchestrator→plan chain.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner
from httpx import ASGITransport

from acheron.api_client import AcheronClient
from acheron.core.models import WorkerCapabilities, WorkerType
from acheron.shell.api.app import create_app
from acheron.shell.cache import PlanCache
from acheron.shell.registry import WorkerRegistry

if TYPE_CHECKING:
    from fastapi import FastAPI


def tts_caps(lang: str = "es") -> WorkerCapabilities:
    return WorkerCapabilities(
        worker_type=WorkerType.TTS,
        supported_languages_in=frozenset({lang}),
        supported_languages_out=frozenset({lang}),
        supported_formats_in=frozenset({"text"}),
        supported_formats_out=frozenset({"wav"}),
        max_payload_bytes=None,
        batch_capable=True,
        model_source=None,
    )


def translation_caps(src: str = "en", dst: str = "es") -> WorkerCapabilities:
    return WorkerCapabilities(
        worker_type=WorkerType.TRANSLATION,
        supported_languages_in=frozenset({src}),
        supported_languages_out=frozenset({dst}),
        supported_formats_in=frozenset({"text"}),
        supported_formats_out=frozenset({"text"}),
        max_payload_bytes=None,
        batch_capable=False,
        model_source=None,
    )


def asr_caps(lang: str = "en") -> WorkerCapabilities:
    return WorkerCapabilities(
        worker_type=WorkerType.ASR,
        supported_languages_in=frozenset({lang}),
        supported_languages_out=frozenset({lang}),
        supported_formats_in=frozenset({"mp3", "wav"}),
        supported_formats_out=frozenset({"text"}),
        max_payload_bytes=None,
        batch_capable=False,
        model_source=None,
    )


def make_app(tmp_path: Path, *, extra_workers: list[tuple[str, str, str, WorkerCapabilities]] | None = None) -> FastAPI:
    """Create a test app with default TTS, translation, and ASR workers."""
    reg = WorkerRegistry()
    reg.register("tts-1", "http://tts", "http", tts_caps())
    reg.register("trans-1", "http://trans", "http", translation_caps())
    reg.register("asr-1", "http://asr", "http", asr_caps())
    for worker_id, endpoint, transport, caps in extra_workers or []:
        reg.register(worker_id, endpoint, transport, caps)
    return create_app(registry=reg, cache=PlanCache(tmp_path), data_dir=tmp_path)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def wired_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """Create a real FastAPI app and wire the CLI to use it via ASGI transport."""
    app = make_app(tmp_path)
    transport = ASGITransport(app=app)
    client = AcheronClient(base_url="http://test", transport=transport)
    monkeypatch.setattr("acheron.cli._get_client", lambda: client)
    return app
