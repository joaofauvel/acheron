"""Tests for data dir handling."""

import os
import stat
from pathlib import Path

import pytest

from acheron.core.errors import AcheronError
from acheron.shell.cache import PlanCache, StepCache
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


@pytest.mark.asyncio
async def test_orchestrator_creates_data_dir_if_missing(tmp_path: Path) -> None:
    """Orchestrator.start() creates the step-cache data dir if it doesn't exist."""
    from acheron.shell.cache import StepCache

    target = tmp_path / "new" / "subdir"
    reg = InMemoryWorkerStore()
    cache = PlanCache(data_dir=target)
    step_cache = StepCache(data_dir=target)
    orch = Orchestrator(registry=reg, cache=cache, step_cache=step_cache)
    try:
        await orch.start()
    finally:
        await orch.close()
    assert target.exists()
    assert target.is_dir()


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write to read-only dirs")
@pytest.mark.asyncio
async def test_orchestrator_raises_on_unwritable_data_dir(tmp_path: Path) -> None:
    """Orchestrator.start() raises AcheronError if the step-cache data dir is not writable."""
    target = tmp_path / "locked"
    target.mkdir()
    target.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)  # read-only
    reg = InMemoryWorkerStore()
    cache = PlanCache(data_dir=target)
    step_cache = StepCache(data_dir=target)
    orch = Orchestrator(registry=reg, cache=cache, step_cache=step_cache)
    try:
        with pytest.raises(AcheronError, match="not writable"):
            await orch.start()
    finally:
        target.chmod(stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)  # restore for cleanup
