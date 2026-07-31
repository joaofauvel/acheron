"""Retention cleanup safety tests."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from acheron.core.errors import CacheError
from acheron.core.models import EpubRequest, ExecutorStrategy, Plan, PlanStatus
from acheron.shell.cache import PlanCache, StepCache, _delete_tree
from acheron.shell.job_store import TrackedJob
from acheron.shell.orchestrator import Orchestrator
from acheron.shell.retention import RetentionPolicy, RetentionService
from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore


def _job(job_id: str, plan_id: str, source: str, status: PlanStatus, now: datetime) -> TrackedJob:
    return TrackedJob(
        job_id=job_id,
        request=EpubRequest(source, "en", "es"),
        strategy=ExecutorStrategy.SEQUENTIAL,
        created_at=now - timedelta(days=30),
        last_persisted_at=now - timedelta(days=30),
        status=status,
        plan=Plan(plan_id, job_id, "epub", "en", "es", ExecutorStrategy.SEQUENTIAL, ()),
    )


async def _service(root: Path, jobs: list[TrackedJob]) -> tuple[RetentionService, InMemoryJobStore]:
    store = InMemoryJobStore()
    for job in jobs:
        persisted_at = job.last_persisted_at
        await store.put(job)
        store._jobs[job.job_id].last_persisted_at = persisted_at  # noqa: SLF001
    return RetentionService(store, PlanCache(root), StepCache(root), data_dir=root), store


@pytest.mark.asyncio
async def test_preview_is_non_mutating_and_reports_relative_sizes(tmp_path: Path) -> None:
    now = datetime(2026, 7, 30, tzinfo=UTC)
    (tmp_path / "job-old").mkdir()
    (tmp_path / "job-old" / "output.bin").write_bytes(b"output")
    (tmp_path / "plan-abcd1234").mkdir()
    (tmp_path / "plan-abcd1234" / "plan.json").write_bytes(b"plan")
    (tmp_path / "inputs" / "id").mkdir(parents=True)
    (tmp_path / "inputs" / "id" / "book.epub").write_bytes(b"input")
    service, store = await _service(
        tmp_path,
        [_job("job-old", "plan-abcd1234", str(tmp_path / "inputs/id/book.epub"), PlanStatus.COMPLETED, now)],
    )

    report = await service.preview(RetentionPolicy(timedelta(days=7), timedelta(days=30)), now=now)

    assert report.deleted_count == 0
    assert report.reclaimable_bytes == 15
    assert report.candidates[0].relative_paths == ("job-old", "plan-abcd1234", "inputs/id/book.epub")
    assert (tmp_path / "job-old").exists()
    assert await store.get("job-old") is not None


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_preview_excludes_active_jobs(tmp_path: Path) -> None:
    now = datetime(2026, 7, 30, tzinfo=UTC)
    (tmp_path / "job-active").mkdir()
    service, _ = await _service(
        tmp_path,
        [_job("job-active", "plan-abcd1234", "missing.epub", PlanStatus.COMPLETED, now)],
    )
    service._active_jobs = lambda: {"job-active"}  # noqa: SLF001

    report = await service.preview(RetentionPolicy(timedelta(days=7), timedelta(days=30)), now=now)

    assert report.candidates == ()
    assert report.reclaimable_bytes == 0


@pytest.mark.asyncio
async def test_preview_does_not_create_missing_data_root(tmp_path: Path) -> None:
    root = tmp_path / "missing"
    service, _ = await _service(root, [])
    assert not root.exists()

    await service.preview(RetentionPolicy(timedelta(days=7), timedelta(days=30)))

    assert not root.exists()


@pytest.mark.asyncio
async def test_preview_skips_malformed_persisted_ids_without_inspection(tmp_path: Path) -> None:
    now = datetime(2026, 7, 30, tzinfo=UTC)
    service, _ = await _service(
        tmp_path,
        [_job("nested/job", "plan-abcd1234", "missing.epub", PlanStatus.COMPLETED, now)],
    )
    service._exists = lambda _relative: pytest.fail("malformed job ID was inspected")  # noqa: SLF001

    report = await service.preview(RetentionPolicy(timedelta(days=7), timedelta(days=30)), now=now)

    assert report.candidates == ()


def test_delete_tree_rejects_intermediate_symlink_swap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    (root / "jobs" / "job-old").mkdir(parents=True)
    (root / "jobs" / "job-old" / "cache").write_bytes(b"inside")
    outside.mkdir()
    (outside / "secret").write_bytes(b"outside")
    original_open = os.open
    swapped = False

    def swap_before_target(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        name = path if isinstance(path, str) else ""
        if name == "job-old" and not swapped:
            swapped = True
            (root / "jobs" / "job-old").rename(root / "jobs" / "job-old-real")
            (root / "jobs" / "job-old").symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr("acheron.shell.cache.os.open", swap_before_target)
    with pytest.raises(CacheError):
        _delete_tree(root, Path("jobs/job-old"))
    assert (outside / "secret").exists()


@pytest.mark.asyncio
async def test_apply_preserves_shared_input_and_refuses_active_job(tmp_path: Path) -> None:
    now = datetime(2026, 7, 30, tzinfo=UTC)
    source = str(tmp_path / "inputs/id/book.epub")
    (tmp_path / "inputs" / "id").mkdir(parents=True)
    (tmp_path / "inputs" / "id" / "book.epub").write_bytes(b"input")
    old = _job("job-old", "plan-abcd1234", source, PlanStatus.COMPLETED, now)
    retained = _job("job-retained", "plan-abcd5678", source, PlanStatus.COMPLETED, now)
    retained.last_persisted_at = now - timedelta(days=1)
    service, store = await _service(tmp_path, [old, retained])
    active = {"job-old"}
    service._active_jobs = lambda: active  # noqa: SLF001

    report = await service.apply(RetentionPolicy(timedelta(days=7), timedelta(days=30)), now=now)

    assert report.deleted_count == 0
    assert report.failures == ()
    assert (tmp_path / "inputs/id/book.epub").exists()
    assert await store.get("job-old") is not None


@pytest.mark.asyncio
async def test_symlink_escape_is_a_retryable_failure(tmp_path: Path) -> None:
    now = datetime(2026, 7, 30, tzinfo=UTC)
    outside = tmp_path.parent / "outside-cleanup-target"
    outside.write_bytes(b"must survive")
    (tmp_path / "job-old").mkdir()
    (tmp_path / "job-old" / "escape").symlink_to(outside)
    service, store = await _service(
        tmp_path,
        [_job("job-old", "plan-abcd1234", str(tmp_path / "missing.epub"), PlanStatus.FAILED, now)],
    )

    report = await service.apply(RetentionPolicy(timedelta(days=7), timedelta(days=1)), now=now)

    assert report.deleted_count == 0
    assert report.failures[0].job_id == "job-old"
    assert await store.get("job-old") is not None
    assert outside.read_bytes() == b"must survive"
    (tmp_path / "job-old" / "escape").unlink()
    retry = await service.apply(RetentionPolicy(timedelta(days=7), timedelta(days=1)), now=now)
    assert retry.deleted_job_ids == ("job-old",)


def test_disk_pressure_is_logged_without_failing_write_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging
    import shutil

    orch = Orchestrator(InMemoryWorkerStore(), PlanCache(tmp_path))
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: shutil._ntuple_diskusage(100, 96, 4))  # noqa: SLF001
    with caplog.at_level(logging.ERROR):
        orch._verify_data_dir_writable()  # noqa: SLF001
    assert "less than 5% free space" in caplog.text
