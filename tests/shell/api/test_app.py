"""Tests for create_app construction symmetry."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient

from acheron.shell.api.app import create_app
from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore

if TYPE_CHECKING:
    pass


def test_create_app_uses_injected_stores_without_consulting_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When both registry and job_store are injected, create_app must use them
    directly and never read ACHERON_STORE_BACKEND."""
    monkeypatch.setenv("ACHERON_STORE_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://this-host-should-never-be-contacted:9999")

    registry = InMemoryWorkerStore()
    job_store = InMemoryJobStore()

    app = create_app(
        registry=registry,
        job_store=job_store,
        cache=None,
        data_dir=tmp_path,
    )

    assert app.state.orchestrator._registry is registry  # noqa: SLF001
    assert app.state.orchestrator._job_store is job_store  # noqa: SLF001


@pytest.mark.asyncio
async def test_conftest_make_app_is_env_independent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared conftest make_app fixture must not depend on the developer's shell env.

    Regression for TEST-004: with ACHERON_STORE_BACKEND=redis exported in the
    dev shell, the old make_app would have constructed a RedisJobStore pointed
    at an unreachable REDIS_URL and all API tests would fail with a connection
    error. The conftest now injects an InMemoryJobStore.
    """
    monkeypatch.setenv("ACHERON_STORE_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://this-host-should-never-be-contacted:9999")

    from tests.shell.conftest import make_app

    app = await make_app(tmp_path)
    await app.state.orchestrator.start()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/health")
            assert r.status_code == 200
    finally:
        await app.state.orchestrator.shutdown()
