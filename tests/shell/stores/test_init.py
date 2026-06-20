"""Tests for store backend selection."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest

from acheron.shell.stores import create_job_store, create_stores, create_worker_store
from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore


@pytest.fixture(autouse=True)
def _clear_backend(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("ACHERON_STORE_BACKEND", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    return


def test_worker_store_default_is_memory() -> None:
    assert isinstance(create_worker_store(), InMemoryWorkerStore)


def test_job_store_default_is_memory() -> None:
    assert isinstance(create_job_store(), InMemoryJobStore)


def test_worker_store_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACHERON_STORE_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://h:1234/0")
    with patch("acheron.shell.stores.redis.RedisWorkerStore") as cls:
        create_worker_store()
        cls.assert_called_once_with("redis://h:1234/0")


def test_job_store_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACHERON_STORE_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://h:1234/0")
    with patch("acheron.shell.stores.redis.RedisJobStore") as cls:
        create_job_store()
        cls.assert_called_once_with("redis://h:1234/0")


def test_unknown_backend_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACHERON_STORE_BACKEND", "sqlite")
    with pytest.raises(ValueError, match="Unknown ACHERON_STORE_BACKEND"):
        create_worker_store()


def test_create_stores_shares_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both stores must observe the same backend in a single call to create_stores()."""
    monkeypatch.setenv("ACHERON_STORE_BACKEND", "memory")
    worker, jobs = create_stores()
    assert isinstance(worker, InMemoryWorkerStore)
    assert isinstance(jobs, InMemoryJobStore)


def test_create_stores_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACHERON_STORE_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://h:1234/0")
    with (
        patch("acheron.shell.stores.redis.RedisWorkerStore") as wcls,
        patch("acheron.shell.stores.redis.RedisJobStore") as jcls,
    ):
        create_stores()
        wcls.assert_called_once_with("redis://h:1234/0")
        jcls.assert_called_once_with("redis://h:1234/0")


def test_create_stores_no_split_brain(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both stores must be on the same backend family — no split-brain."""
    monkeypatch.setenv("ACHERON_STORE_BACKEND", "memory")
    worker, jobs = create_stores()
    worker_is_memory = isinstance(worker, InMemoryWorkerStore)
    jobs_is_memory = isinstance(jobs, InMemoryJobStore)
    assert worker_is_memory is jobs_is_memory
