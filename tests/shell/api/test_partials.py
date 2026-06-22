"""Tests for the orchestrator HTML partial endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from acheron.shell.api.app import create_app
from acheron.shell.cache import PlanCache
from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest_asyncio.fixture
async def client(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    app = create_app(
        registry=InMemoryWorkerStore(),
        job_store=InMemoryJobStore(),
        cache=PlanCache(tmp_path),
        data_dir=tmp_path,
    )
    await app.state.orchestrator.start()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
        await app.state.orchestrator.shutdown()


class TestStatusPartial:
    @pytest.mark.asyncio
    async def test_returns_connected_html(self, client: AsyncClient) -> None:
        resp = await client.get("/partials/status")
        assert resp.status_code == 200
        assert "Connected" in resp.text
        assert "dot-green" in resp.text
