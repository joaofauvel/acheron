"""Tests for data dir handling."""

import os
import stat
from pathlib import Path

import pytest

from acheron.core.errors import AcheronError
from acheron.shell.cache import PlanCache
from acheron.shell.orchestrator import Orchestrator
from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore


def test_data_dir_is_public_attribute(tmp_path: Path) -> None:
    """PlanCache exposes its data_dir for startup checks."""
    cache = PlanCache(data_dir=tmp_path)
    assert cache.data_dir == tmp_path


def test_create_app_reads_acheron_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """create_app falls back to ACHERON_DATA_DIR when data_dir not passed."""
    from acheron.shell.api.app import create_app

    monkeypatch.setenv("ACHERON_DATA_DIR", str(tmp_path))
    app = create_app(registry=InMemoryWorkerStore(), job_store=InMemoryJobStore())
    assert app.state.orchestrator._cache.data_dir == tmp_path  # noqa: SLF001


def test_orchestrator_creates_data_dir_if_missing(tmp_path: Path) -> None:
    """Orchestrator creates the data dir if it doesn't exist."""
    target = tmp_path / "new" / "subdir"
    reg = InMemoryWorkerStore()
    cache = PlanCache(data_dir=target)
    Orchestrator(registry=reg, cache=cache)
    assert target.exists()
    assert target.is_dir()


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write to read-only dirs")
def test_orchestrator_raises_on_unwritable_data_dir(tmp_path: Path) -> None:
    """Orchestrator raises AcheronError if data dir is not writable."""
    target = tmp_path / "locked"
    target.mkdir()
    target.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)  # read-only
    reg = InMemoryWorkerStore()
    cache = PlanCache(data_dir=target)
    try:
        with pytest.raises(AcheronError, match="not writable"):
            Orchestrator(registry=reg, cache=cache)
    finally:
        target.chmod(stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)  # restore for cleanup
