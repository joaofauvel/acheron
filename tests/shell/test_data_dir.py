"""Tests for data dir handling."""

from pathlib import Path

import pytest

from acheron.shell.cache import PlanCache


def test_data_dir_is_public_attribute(tmp_path: Path) -> None:
    """PlanCache exposes its data_dir for startup checks."""
    cache = PlanCache(data_dir=tmp_path)
    assert cache.data_dir == tmp_path


def test_create_app_reads_acheron_data_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """create_app falls back to ACHERON_DATA_DIR when data_dir not passed."""
    from acheron.shell.api.app import create_app
    from acheron.shell.stores.memory import InMemoryWorkerStore

    monkeypatch.setenv("ACHERON_DATA_DIR", str(tmp_path))
    app = create_app(registry=InMemoryWorkerStore())
    assert app.state.orchestrator._cache.data_dir == tmp_path  # noqa: SLF001
