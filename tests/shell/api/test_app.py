"""Tests for create_app construction symmetry."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from acheron.shell.api.app import _safe_request_id, create_app
from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore


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
async def test_chunked_json_body_is_bounded(tmp_path: Path) -> None:
    from tests.shell.conftest import make_app

    app = await make_app(tmp_path)

    async def body() -> AsyncIterator[bytes]:
        yield b"{" + b'"payload":"' + (b"x" * (8 * 1024 * 1024)) + b'"}'

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/jobs", content=body(), headers={"content-type": "application/json"})
    assert response.status_code == 413
    assert response.json() == {"detail": "request body exceeds the supported limit"}


@pytest.mark.asyncio
async def test_unexpected_error_keeps_request_id_and_safe_body(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.shell.conftest import make_app

    app = await make_app(tmp_path)

    async def failing_list_workers() -> tuple[object, ...]:
        raise RuntimeError("secret")

    monkeypatch.setattr(app.state.orchestrator, "list_workers", failing_list_workers)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/workers", headers={"x-request-id": "req-123"})
    assert response.status_code == 500
    assert response.headers["x-request-id"] == "req-123"
    assert response.json() == {"detail": "Request failed"}


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
            assert r.headers["x-request-id"]
    finally:
        await app.state.orchestrator.shutdown()


def test_request_id_validation_rejects_header_injection() -> None:
    assert _safe_request_id("req-test")
    assert not _safe_request_id("req\r\nX-Leak: yes")
    assert not _safe_request_id("req\x00test")


@pytest.mark.asyncio
async def test_request_id_header_preserves_client_id(tmp_path: Path) -> None:
    app = create_app(
        registry=InMemoryWorkerStore(),
        job_store=InMemoryJobStore(),
        data_dir=tmp_path,
    )
    await app.state.orchestrator.start()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health", headers={"x-request-id": "req-test"})
            assert response.headers["x-request-id"] == "req-test"
    finally:
        await app.state.orchestrator.shutdown()
