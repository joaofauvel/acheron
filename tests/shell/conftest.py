"""Shared test fixtures for shell tests."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest  # noqa: TC002
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from acheron.core.models import WorkerCapabilities, WorkerType
from acheron.shell.api.app import create_app
from acheron.shell.cache import PlanCache
from acheron.shell.stores.memory import InMemoryWorkerStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI


def tts_caps(lang: str = "es") -> WorkerCapabilities:
    """Create TTS worker capabilities for testing."""
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
    """Create translation worker capabilities for testing."""
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
    """Create ASR worker capabilities for testing."""
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


async def make_app(tmp_path: Path) -> FastAPI:
    """Create a test app with TTS and translation workers registered."""
    reg = InMemoryWorkerStore()
    await reg.register("tts-1", "http://tts", "http", tts_caps())
    await reg.register("trans-1", "http://trans", "http", translation_caps())
    return create_app(registry=reg, cache=PlanCache(tmp_path), data_dir=tmp_path)


@pytest_asyncio.fixture
async def client(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    """Create an async HTTP client for testing the API."""
    app = await make_app(tmp_path)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def client_with_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    """Create an async client with registration token enabled."""
    monkeypatch.setenv("ACHERON_REGISTRATION_TOKEN", "test-token")
    app = await make_app(tmp_path)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
