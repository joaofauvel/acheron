"""Tests for create_app construction symmetry."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from acheron.shell.api.app import create_app
from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore

if TYPE_CHECKING:
    import pytest


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
