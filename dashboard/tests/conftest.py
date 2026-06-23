"""Shared fixtures for the dashboard tests."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from dashboard.app import create_app

_ORCH_URL = "http://orchestrator:8000"


@pytest.fixture
def app():
    return create_app(orchestrator_url=_ORCH_URL)


@pytest_asyncio.fixture()
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
