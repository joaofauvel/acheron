"""Tests for deployed version identity."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import AsyncClient

from acheron.shell.api.app import create_app
from acheron.shell.cache import PlanCache
from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore
from acheron.version import VersionInfo, build_version


def test_version_response_uses_explicit_build_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACHERON_BUILD_SHA", "abc1234")
    monkeypatch.setenv("ACHERON_BUILD_TIME", "2026-07-30T12:00:00Z")
    monkeypatch.setenv("ACHERON_BUILD_BRANCH", "master")
    monkeypatch.setenv("ACHERON_BUILD_DIRTY", "false")

    version = build_version()

    assert version.sha == "abc1234"
    assert version.build_time == datetime(2026, 7, 30, 12, tzinfo=UTC)
    assert version.dirty is False


@pytest.mark.asyncio
async def test_version_endpoint_omits_environment_dump_and_unset_build_values(client: AsyncClient) -> None:
    response = await client.get("/version")

    assert response.status_code == 200
    body = response.json()
    assert body["version"]
    assert body["sha"] is None
    assert body["build_time"] is None
    assert body["branch"] is None
    assert body["dirty"] is None
    assert body["image"] is None
    assert body["registry"] is None
    assert set(body) == {"version", "sha", "build_time", "branch", "dirty", "image", "registry"}
    assert "environment" not in body
    assert "ACHERON_BUILD_SHA" not in response.text


@pytest.mark.asyncio
async def test_version_endpoint_redacts_unsafe_image_and_registry(tmp_path: Path) -> None:
    app = create_app(
        registry=InMemoryWorkerStore(),
        job_store=InMemoryJobStore(),
        cache=PlanCache(tmp_path),
        data_dir=tmp_path,
    )
    app.state.version = VersionInfo(
        version="1.0.0",
        sha="password=TOPSECRET",
        build_time=None,
        branch="https://token:secret@evil/main",
        dirty=False,
        image="https://registry.example/private?token=TOPSECRET",
        registry="/etc/acheron/credentials",
    )
    from httpx import ASGITransport

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/version")

    assert response.status_code == 200
    body = response.json()
    assert body["image"] is None
    assert body["registry"] is None
    assert body["sha"] == "unknown"
    assert body["branch"] == "unknown"
    assert "TOPSECRET" not in response.text


def test_malformed_dirty_value_is_rejected_during_app_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ACHERON_BUILD_DIRTY", "yes")

    with pytest.raises(ValueError, match="ACHERON_BUILD_DIRTY") as exc_info:
        create_app(
            registry=InMemoryWorkerStore(),
            job_store=InMemoryJobStore(),
            cache=PlanCache(tmp_path),
            data_dir=tmp_path,
        )

    assert isinstance(exc_info.value.__cause__, KeyError)
