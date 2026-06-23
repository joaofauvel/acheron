"""Shared fixtures for the 7-stub SDK matrix tests."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient


@pytest_asyncio.fixture
async def http_client() -> AsyncIterator[AsyncClient]:
    """Plain httpx ASGI client factory; each test gets its own transport."""
    transport = ASGITransport(app=None)  # placeholder, set per test
    client = AsyncClient(transport=transport, base_url="http://test")
    yield client
    await client.aclose()
