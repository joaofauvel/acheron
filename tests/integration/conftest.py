"""Shared fixtures for integration tests.

Integration tests exercise the CLI against a real FastAPI app via ASGI transport,
verifying the full request→route→orchestrator→plan chain.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from click.testing import CliRunner
from httpx import ASGITransport

from acheron.api_client import AcheronClient
from acheron.core.models import Job, JobMetrics, JobResult, JobStatus, OutputFile, WorkerCapabilities, WorkerType
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


async def _start_uvicorn(app_factory) -> tuple[str, asyncio.Task[None]]:  # type: ignore[no-untyped-def]
    """Start a FastAPI app with uvicorn as a background task using a random port."""
    from contextlib import asynccontextmanager

    import uvicorn
    from fastapi import FastAPI

    original_app = app_factory()

    @asynccontextmanager
    async def _noop_lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield

    app = FastAPI(title=original_app.title, lifespan=_noop_lifespan)
    for route in original_app.routes:
        app.router.routes.append(route)

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    await asyncio.sleep(0.3)

    actual_port = 0
    if server.servers and server.servers[0].sockets:
        actual_port = server.servers[0].sockets[0].getsockname()[1]

    return f"http://127.0.0.1:{actual_port}", task


@pytest_asyncio.fixture
async def http_tts_stub() -> AsyncIterator[str]:
    """Start a TTS HTTP stub worker."""

    os.environ["WORKER_TYPE"] = "TTS"
    os.environ["WORKER_ENDPOINT"] = "http://127.0.0.1:0"
    os.environ["ORCHESTRATOR_URL"] = "http://127.0.0.1:1"
    os.environ["WORKER_PORT"] = "0"
    os.environ["ACHERON_REGISTRATION_TOKEN"] = ""

    from stubs.worker_stub import create_app

    url, task = await _start_uvicorn(create_app)
    yield url
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest_asyncio.fixture
async def http_translation_stub() -> AsyncIterator[str]:
    """Start a translation HTTP stub worker."""

    os.environ["WORKER_TYPE"] = "TRANSLATION"
    os.environ["WORKER_ENDPOINT"] = "http://127.0.0.1:0"
    os.environ["ORCHESTRATOR_URL"] = "http://127.0.0.1:1"
    os.environ["WORKER_PORT"] = "0"
    os.environ["ACHERON_REGISTRATION_TOKEN"] = ""

    from stubs.translation_stub import create_app

    url, task = await _start_uvicorn(create_app)
    yield url
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


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

    async def _mock_handler(job: Job) -> JobResult:
        return JobResult(
            job_id=job.job_id,
            status=JobStatus.SUCCESS,
            outputs=(
                OutputFile(
                    path=f"/tmp/{job.job_id}",
                    filename=f"{job.job_id}.dat",
                    size_bytes=100,
                    checksum="abc",
                    content_type="application/octet-stream",
                ),
            ),
            metrics=JobMetrics(duration_seconds=0.01),
        )

    reg = WorkerRegistry()
    cache = PlanCache(tmp_path)

    reg.register(
        "extract-local",
        "local",
        "local",
        WorkerCapabilities(
            worker_type=WorkerType.EXTRACTION,
            supported_languages_in=frozenset(),
            supported_languages_out=frozenset(),
            supported_formats_in=frozenset(),
            supported_formats_out=frozenset(),
            max_payload_bytes=None,
            batch_capable=False,
            model_source=None,
        ),
        metadata={"handler": _mock_handler},
    )

    reg.register(
        "chunk-local",
        "local",
        "local",
        WorkerCapabilities(
            worker_type=WorkerType.CHUNKING,
            supported_languages_in=frozenset(),
            supported_languages_out=frozenset(),
            supported_formats_in=frozenset(),
            supported_formats_out=frozenset(),
            max_payload_bytes=None,
            batch_capable=False,
            model_source=None,
        ),
        metadata={"handler": _mock_handler},
    )

    reg.register(
        "package-local",
        "local",
        "local",
        WorkerCapabilities(
            worker_type=WorkerType.PACKAGING,
            supported_languages_in=frozenset(),
            supported_languages_out=frozenset(),
            supported_formats_in=frozenset(),
            supported_formats_out=frozenset(),
            max_payload_bytes=None,
            batch_capable=False,
            model_source=None,
        ),
        metadata={"handler": _mock_handler},
    )

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
