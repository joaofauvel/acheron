"""Shared fixtures for store tests."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
import redis.asyncio
from testcontainers.redis import RedisContainer


@pytest.fixture(scope="session")
def redis_container() -> RedisContainer:
    container = RedisContainer("redis:7-alpine")
    container.start()
    return container


@pytest_asyncio.fixture
async def redis_url(redis_container: RedisContainer) -> AsyncIterator[str]:
    """Yield a Redis URL and FLUSHDB the database before each test."""
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    url = f"redis://{host}:{port}"

    client = redis.asyncio.Redis.from_url(url, decode_responses=True)
    try:
        await client.flushdb()
    finally:
        await client.aclose()

    yield url
