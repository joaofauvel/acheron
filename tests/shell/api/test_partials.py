"""Tests for the orchestrator HTML partial endpoints."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from acheron.core.models import WorkerCapabilities, WorkerStatus, WorkerType
from acheron.shell.api.app import create_app
from acheron.shell.cache import PlanCache
from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI


@pytest_asyncio.fixture
async def app_context(tmp_path: Path) -> AsyncIterator[tuple[FastAPI, InMemoryWorkerStore]]:
    registry = InMemoryWorkerStore()
    app = create_app(
        registry=registry,
        job_store=InMemoryJobStore(),
        cache=PlanCache(tmp_path),
        data_dir=tmp_path,
    )
    await app.state.orchestrator.start()
    try:
        builtin_worker_ids = ("extraction-local", "chunking-local", "packaging-local")
        for worker_id in builtin_worker_ids:
            await registry.set_worker_status(worker_id, WorkerStatus.OFFLINE, None)
        async with asyncio.timeout(1):
            while True:
                workers = {worker.worker_id: worker for worker in await registry.list_all()}
                if all(workers[worker_id].status is WorkerStatus.HEALTHY for worker_id in builtin_worker_ids):
                    break
                await asyncio.sleep(0)
        yield app, registry
    finally:
        await app.state.orchestrator.shutdown()


@pytest_asyncio.fixture
async def client(app_context: tuple[FastAPI, InMemoryWorkerStore]) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app_context[0])
    async with AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client


@pytest.fixture
def registry(app_context: tuple[FastAPI, InMemoryWorkerStore]) -> InMemoryWorkerStore:
    return app_context[1]


async def _register_worker(
    registry: InMemoryWorkerStore,
    worker_id: str,
    worker_type: WorkerType,
    status: WorkerStatus = WorkerStatus.HEALTHY,
) -> None:
    await registry.register(
        worker_id=worker_id,
        endpoint=f"http://{worker_id}:8000",
        transport="http",
        capabilities=WorkerCapabilities(
            worker_type=worker_type,
            supported_languages_in=frozenset({"en"}),
            supported_languages_out=frozenset({"es"}),
            supported_formats_in=frozenset({"text"}),
            supported_formats_out=frozenset({"wav"}),
            max_payload_bytes=None,
            batch_capable=False,
            model_source=None,
        ),
    )
    if status is not WorkerStatus.HEALTHY:
        await registry.set_worker_status(worker_id, status, None)


class TestStatusPartial:
    @pytest.mark.asyncio
    async def test_no_tts_workers_report_zero_of_zero(self, client: AsyncClient) -> None:
        response = await client.get("/partials/status")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert response.text == '<span class="dot dot-yellow"></span> Waiting (0/0 TTS healthy)'

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("statuses", "expected"),
        [
            (
                (WorkerStatus.BOOTING, WorkerStatus.OFFLINE, WorkerStatus.BOOTING),
                '<span class="dot dot-yellow"></span> Waiting (0/3 TTS healthy)',
            ),
            (
                (WorkerStatus.HEALTHY, WorkerStatus.BOOTING, WorkerStatus.OFFLINE),
                '<span class="dot dot-yellow"></span> Waiting (1/3 TTS healthy)',
            ),
            (
                (WorkerStatus.HEALTHY, WorkerStatus.HEALTHY, WorkerStatus.HEALTHY),
                '<span class="dot dot-green"></span> Ready (3/3 TTS healthy)',
            ),
        ],
    )
    async def test_tts_readiness_counts_healthy_workers(
        self,
        client: AsyncClient,
        registry: InMemoryWorkerStore,
        statuses: tuple[WorkerStatus, WorkerStatus, WorkerStatus],
        expected: str,
    ) -> None:
        for index, status in enumerate(statuses):
            await _register_worker(registry, f"tts-{index}", WorkerType.TTS, status)

        response = await client.get("/partials/status")

        assert response.text == expected

    @pytest.mark.asyncio
    async def test_asr_only_fleet_is_not_ready(
        self,
        client: AsyncClient,
        registry: InMemoryWorkerStore,
    ) -> None:
        await _register_worker(registry, "asr-1", WorkerType.ASR)

        response = await client.get("/partials/status")

        assert response.text == '<span class="dot dot-yellow"></span> Waiting (0/0 TTS healthy)'

    @pytest.mark.asyncio
    async def test_healthy_mixed_service_fleet_is_ready(
        self,
        client: AsyncClient,
        registry: InMemoryWorkerStore,
    ) -> None:
        await _register_worker(registry, "tts-1", WorkerType.TTS)
        await _register_worker(registry, "asr-1", WorkerType.ASR)
        await _register_worker(registry, "translation-1", WorkerType.TRANSLATION)

        response = await client.get("/partials/status")

        assert response.text == '<span class="dot dot-green"></span> Ready (1/1 TTS healthy)'

    @pytest.mark.asyncio
    @pytest.mark.parametrize("unhealthy_worker_type", [WorkerType.ASR, WorkerType.TRANSLATION])
    async def test_unhealthy_non_tts_service_worker_blocks_readiness(
        self,
        client: AsyncClient,
        registry: InMemoryWorkerStore,
        unhealthy_worker_type: WorkerType,
    ) -> None:
        worker_ids = {
            WorkerType.TTS: "tts-1",
            WorkerType.ASR: "asr-1",
            WorkerType.TRANSLATION: "translation-1",
        }
        for worker_type, worker_id in worker_ids.items():
            await _register_worker(registry, worker_id, worker_type)
        await registry.set_worker_status(worker_ids[unhealthy_worker_type], WorkerStatus.BOOTING, None)

        response = await client.get("/partials/status")

        assert response.text == '<span class="dot dot-yellow"></span> Waiting (1/1 TTS healthy)'

    @pytest.mark.asyncio
    async def test_unhealthy_builtin_workers_do_not_block_readiness(
        self,
        client: AsyncClient,
        registry: InMemoryWorkerStore,
    ) -> None:
        for worker_id in ("extraction-local", "chunking-local", "packaging-local"):
            await registry.set_worker_status(worker_id, WorkerStatus.OFFLINE, None)
        await _register_worker(registry, "tts-1", WorkerType.TTS)

        response = await client.get("/partials/status")

        assert response.text == '<span class="dot dot-green"></span> Ready (1/1 TTS healthy)'
