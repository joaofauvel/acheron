"""Shared fixtures for store tests."""

from __future__ import annotations

import pytest
import redis
from testcontainers.redis import RedisContainer


@pytest.fixture(scope="session")
def redis_container() -> RedisContainer:
    container = RedisContainer("redis:7-alpine")
    container.start()
    return container


@pytest.fixture
def redis_url(redis_container: RedisContainer) -> str:
    """Yield a Redis URL and FLUSHDB the database before each test."""
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    url = f"redis://{host}:{port}"
    client = redis.Redis.from_url(url)
    client.flushdb()
    client.close()
    return url
