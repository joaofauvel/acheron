"""Shared fixtures for integration tests.

Integration tests exercise the CLI against a real FastAPI app via ASGI transport,
verifying the full request→route→orchestrator→plan chain.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import asyncio  # noqa: TC003
import os

import pytest  # noqa: TC002
import pytest_asyncio  # noqa: TC002
from click.testing import CliRunner
from httpx import ASGITransport

from acheron.api_client import AcheronClient
from acheron.core.models import WorkerCapabilities, WorkerType
from acheron.shell.api.app import create_app
from acheron.shell.cache import PlanCache
from acheron.shell.orchestrator import Orchestrator
from acheron.shell.registry import WorkerRegistry
from acheron.shell.step_handler import create_step_handler

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


async def _start_uvicorn(app_factory, port: int) -> tuple[str, asyncio.Task[None]]:
    """Start a FastAPI app with uvicorn as a background task."""
    import uvicorn

    app = app_factory()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    await asyncio.sleep(0.5)
    return f"http://127.0.0.1:{port}", task


@pytest_asyncio.fixture
async def http_tts_stub() -> AsyncIterator[str]:
    """Start a TTS HTTP stub worker."""
    from stubs.worker_stub import create_app

    orig_type = os.environ.get("WORKER_TYPE")
    orig_endpoint = os.environ.get("WORKER_ENDPOINT")
    orig_orch = os.environ.get("ORCHESTRATOR_URL")
    orig_port = os.environ.get("WORKER_PORT")
    orig_token = os.environ.get("ACHERON_REGISTRATION_TOKEN")

    os.environ["WORKER_TYPE"] = "TTS"
    os.environ["WORKER_ENDPOINT"] = "http://127.0.0.1:18001"
    os.environ["ORCHESTRATOR_URL"] = "http://127.0.0.1:1"
    os.environ["WORKER_PORT"] = "18001"
    os.environ["ACHERON_REGISTRATION_TOKEN"] = ""

    url, task = await _start_uvicorn(create_app, 18001)
    yield url
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    if orig_type is not None:
        os.environ["WORKER_TYPE"] = orig_type
    else:
        os.environ.pop("WORKER_TYPE", None)
    if orig_endpoint is not None:
        os.environ["WORKER_ENDPOINT"] = orig_endpoint
    else:
        os.environ.pop("WORKER_ENDPOINT", None)
    if orig_orch is not None:
        os.environ["ORCHESTRATOR_URL"] = orig_orch
    else:
        os.environ.pop("ORCHESTRATOR_URL", None)
    if orig_port is not None:
        os.environ["WORKER_PORT"] = orig_port
    else:
        os.environ.pop("WORKER_PORT", None)
    if orig_token is not None:
        os.environ["ACHERON_REGISTRATION_TOKEN"] = orig_token
    else:
        os.environ.pop("ACHERON_REGISTRATION_TOKEN", None)


@pytest_asyncio.fixture
async def http_translation_stub() -> AsyncIterator[str]:
    """Start a translation HTTP stub worker."""
    from stubs.translation_stub import create_app

    orig_type = os.environ.get("WORKER_TYPE")
    orig_endpoint = os.environ.get("WORKER_ENDPOINT")
    orig_orch = os.environ.get("ORCHESTRATOR_URL")
    orig_port = os.environ.get("WORKER_PORT")
    orig_token = os.environ.get("ACHERON_REGISTRATION_TOKEN")

    os.environ["WORKER_TYPE"] = "TRANSLATION"
    os.environ["WORKER_ENDPOINT"] = "http://127.0.0.1:18003"
    os.environ["ORCHESTRATOR_URL"] = "http://127.0.0.1:1"
    os.environ["WORKER_PORT"] = "18003"
    os.environ["ACHERON_REGISTRATION_TOKEN"] = ""

    url, task = await _start_uvicorn(create_app, 18003)
    yield url
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    if orig_type is not None:
        os.environ["WORKER_TYPE"] = orig_type
    else:
        os.environ.pop("WORKER_TYPE", None)
    if orig_endpoint is not None:
        os.environ["WORKER_ENDPOINT"] = orig_endpoint
    else:
        os.environ.pop("WORKER_ENDPOINT", None)
    if orig_orch is not None:
        os.environ["ORCHESTRATOR_URL"] = orig_orch
    else:
        os.environ.pop("ORCHESTRATOR_URL", None)
    if orig_port is not None:
        os.environ["WORKER_PORT"] = orig_port
    else:
        os.environ.pop("WORKER_PORT", None)
    if orig_token is not None:
        os.environ["ACHERON_REGISTRATION_TOKEN"] = orig_token
    else:
        os.environ.pop("ACHERON_REGISTRATION_TOKEN", None)


@pytest_asyncio.fixture
async def grpc_tts_stub() -> AsyncIterator[str]:
    """Start a TTS gRPC stub worker."""
    from stubs.grpc_worker_stub import create_server

    server, port = await create_server(port=0, register=False)
    await server.start()
    yield f"localhost:{port}"
    await server.stop(0)


@pytest_asyncio.fixture
async def wired_orchestrator(
    tmp_path: Path,
    http_tts_stub: str,
    http_translation_stub: str,
    grpc_tts_stub: str,
) -> AsyncIterator[Orchestrator]:
    """Orchestrator with real stub workers registered."""
    reg = WorkerRegistry()
    cache = PlanCache(tmp_path)

    reg.register(
        "tts-http",
        http_tts_stub,
        "http",
        WorkerCapabilities(
            worker_type=WorkerType.TTS,
            supported_languages_in=frozenset({"en", "es", "fr", "de"}),
            supported_languages_out=frozenset({"en", "es", "fr", "de"}),
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"wav", "pcm"}),
            max_payload_bytes=None,
            batch_capable=True,
            model_source=None,
        ),
    )

    reg.register(
        "tts-grpc",
        grpc_tts_stub,
        "grpc",
        WorkerCapabilities(
            worker_type=WorkerType.TTS,
            supported_languages_in=frozenset({"en", "es", "fr", "de"}),
            supported_languages_out=frozenset({"en", "es", "fr", "de"}),
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"wav", "pcm"}),
            max_payload_bytes=None,
            batch_capable=True,
            model_source=None,
        ),
    )

    reg.register(
        "trans-http",
        http_translation_stub,
        "http",
        WorkerCapabilities(
            worker_type=WorkerType.TRANSLATION,
            supported_languages_in=frozenset({"en", "es", "fr", "de"}),
            supported_languages_out=frozenset({"en", "es", "fr", "de"}),
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"text"}),
            max_payload_bytes=None,
            batch_capable=False,
            model_source=None,
        ),
    )

    handler = create_step_handler(reg)
    orch = Orchestrator(registry=reg, cache=cache, handler=handler)
    yield orch
