"""Shared fixtures for integration tests.

Integration tests exercise the CLI against a real FastAPI app via ASGI transport,
verifying the full request→route→orchestrator→plan chain.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
from collections.abc import AsyncIterator, Callable, Coroutine
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio
from click.testing import CliRunner
from httpx import ASGITransport

from acheron.api_client import AcheronClient
from acheron.core.models import Job, JobMetrics, JobResult, JobStatus, OutputFile, WorkerCapabilities, WorkerType
from acheron.shell.api.app import create_app
from acheron.shell.cache import PlanCache
from acheron.shell.orchestrator import Orchestrator
from acheron.shell.stores.memory import InMemoryWorkerStore

if TYPE_CHECKING:
    from fastapi import FastAPI

type JobHandler = Callable[[Job], Coroutine[Any, Any, JobResult]]


def _caps(  # noqa: PLR0913
    worker_type: WorkerType,
    *,
    langs_in: frozenset[str] = frozenset(),
    langs_out: frozenset[str] = frozenset(),
    formats_in: frozenset[str] = frozenset(),
    formats_out: frozenset[str] = frozenset(),
    batch_capable: bool = False,
) -> WorkerCapabilities:
    return WorkerCapabilities(
        worker_type=worker_type,
        supported_languages_in=langs_in,
        supported_languages_out=langs_out,
        supported_formats_in=formats_in,
        supported_formats_out=formats_out,
        max_payload_bytes=None,
        batch_capable=batch_capable,
        model_source=None,
    )


def _mock_handler(job: Job) -> Coroutine[Any, Any, JobResult]:
    async def _run() -> JobResult:
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

    return _run()


def tts_caps(lang: str = "es") -> WorkerCapabilities:
    return _caps(WorkerType.TTS, langs_in=frozenset({lang}), langs_out=frozenset({lang}), batch_capable=True)


def translation_caps(src: str = "en", dst: str = "es") -> WorkerCapabilities:
    return _caps(WorkerType.TRANSLATION, langs_in=frozenset({src}), langs_out=frozenset({dst}))


def asr_caps(lang: str = "en") -> WorkerCapabilities:
    return _caps(WorkerType.ASR, langs_in=frozenset({lang}), langs_out=frozenset({lang}))


async def make_app(
    tmp_path: Path, *, extra_workers: list[tuple[str, str, str, WorkerCapabilities]] | None = None
) -> FastAPI:
    """Create a test app with default TTS, translation, and ASR workers."""
    reg = InMemoryWorkerStore()
    await reg.register("tts-1", "http://127.0.0.1:1", "http", tts_caps())
    await reg.register("trans-1", "http://127.0.0.1:2", "http", translation_caps())
    await reg.register("asr-1", "http://127.0.0.1:3", "http", asr_caps())
    for worker_id, endpoint, transport, caps in extra_workers or []:
        await reg.register(worker_id, endpoint, transport, caps)
    return create_app(registry=reg, cache=PlanCache(tmp_path), data_dir=tmp_path)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest_asyncio.fixture
async def wired_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[FastAPI]:
    """Create a real FastAPI app and wire the CLI to use it via ASGI transport.

    Calls ``orchestrator.start()`` explicitly because httpx's ASGITransport does
    not trigger the FastAPI lifespan, so local workers would otherwise not be
    registered before the first request. Teardown mirrors the production
    lifespan: shutdown the health monitor, then close stores.
    """
    app = await make_app(tmp_path)
    await app.state.orchestrator.start()
    transport = ASGITransport(app=app)
    client = AcheronClient(base_url="http://test", transport=transport)
    monkeypatch.setattr("acheron.cli._get_client", lambda: client)
    yield app
    await app.state.orchestrator.shutdown()
    await app.state.orchestrator.close()


async def _wait_for_port(host: str, port: int, timeout: float = 2.0) -> None:  # noqa: ASYNC109
    """Poll until a port accepts connections."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        try:
            sock = socket.create_connection((host, port), timeout=0.1)
            sock.close()
        except OSError:
            await asyncio.sleep(0.05)
        else:
            return
    msg = f"Port {host}:{port} not ready after {timeout}s"
    raise TimeoutError(msg)


async def _start_uvicorn(app_factory: Callable[[], FastAPI]) -> tuple[str, asyncio.Task[None]]:
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

    actual_port = 0
    for _ in range(60):
        await asyncio.sleep(0.05)
        if server.servers and server.servers[0].sockets:
            actual_port = server.servers[0].sockets[0].getsockname()[1]
            break

    if actual_port:
        await _wait_for_port("127.0.0.1", actual_port)

    return f"http://127.0.0.1:{actual_port}", task


@pytest_asyncio.fixture
async def http_tts_stub() -> AsyncIterator[str]:
    """Start a TTS HTTP stub worker."""
    import os

    saved = {
        k: os.environ.get(k)
        for k in ("WORKER_TYPE", "WORKER_ENDPOINT", "ORCHESTRATOR_URL", "WORKER_PORT", "ACHERON_REGISTRATION_TOKEN")
    }
    os.environ["WORKER_TYPE"] = "TTS"
    os.environ["WORKER_ENDPOINT"] = "http://127.0.0.1:0"
    os.environ["ORCHESTRATOR_URL"] = "http://127.0.0.1:1"
    os.environ["WORKER_PORT"] = "0"
    os.environ["ACHERON_REGISTRATION_TOKEN"] = ""

    try:
        from stubs.worker_stub import create_app

        url, task = await _start_uvicorn(create_app)
        yield url
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest_asyncio.fixture
async def http_translation_stub() -> AsyncIterator[str]:
    """Start a translation HTTP stub worker."""
    import os

    saved = {
        k: os.environ.get(k)
        for k in ("WORKER_TYPE", "WORKER_ENDPOINT", "ORCHESTRATOR_URL", "WORKER_PORT", "ACHERON_REGISTRATION_TOKEN")
    }
    os.environ["WORKER_TYPE"] = "TRANSLATION"
    os.environ["WORKER_ENDPOINT"] = "http://127.0.0.1:0"
    os.environ["ORCHESTRATOR_URL"] = "http://127.0.0.1:1"
    os.environ["WORKER_PORT"] = "0"
    os.environ["ACHERON_REGISTRATION_TOKEN"] = ""

    try:
        from stubs.translation_stub import create_app

        url, task = await _start_uvicorn(create_app)
        yield url
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest_asyncio.fixture
async def grpc_tts_stub() -> AsyncIterator[str]:
    """Start a TTS gRPC stub worker."""
    from stubs.grpc_worker_stub import create_server

    server, port = await create_server(port=0, register=False)
    await server.start()
    yield f"localhost:{port}"
    await server.stop(0)


_LANGS = frozenset({"en", "es", "fr", "de"})


@pytest_asyncio.fixture
async def wired_orchestrator(
    tmp_path: Path,
    http_tts_stub: str,
    http_translation_stub: str,
    grpc_tts_stub: str,
) -> AsyncIterator[Orchestrator]:
    """Orchestrator with real stub workers registered.

    EXTRACTION/CHUNKING/PACKAGING are auto-registered by the orchestrator as
    built-in local workers (with their own handlers).
    """

    reg = InMemoryWorkerStore()

    await reg.register(
        "tts-http",
        http_tts_stub,
        "http",
        _caps(
            WorkerType.TTS,
            langs_in=_LANGS,
            langs_out=_LANGS,
            formats_in=frozenset({"text"}),
            formats_out=frozenset({"wav", "pcm"}),
            batch_capable=True,
        ),
    )
    await reg.register(
        "tts-grpc",
        grpc_tts_stub,
        "grpc",
        _caps(
            WorkerType.TTS,
            langs_in=_LANGS,
            langs_out=_LANGS,
            formats_in=frozenset({"text"}),
            formats_out=frozenset({"wav", "pcm"}),
            batch_capable=True,
        ),
    )
    await reg.register(
        "trans-http",
        http_translation_stub,
        "http",
        _caps(
            WorkerType.TRANSLATION,
            langs_in=_LANGS,
            langs_out=_LANGS,
            formats_in=frozenset({"text"}),
            formats_out=frozenset({"text"}),
        ),
    )

    orch = Orchestrator(registry=reg, cache=PlanCache(tmp_path))
    await orch.start()
    yield orch
    await orch.shutdown()
