"""Tests for the store factory functions."""

import pytest

from acheron.shell.stores import create_job_store, create_worker_store
from acheron.shell.stores.memory import InMemoryJobStore, InMemoryWorkerStore


class TestCreateWorkerStore:
    def test_defaults_to_memory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ACHERON_STORE_BACKEND", raising=False)
        store = create_worker_store()
        assert isinstance(store, InMemoryWorkerStore)

    def test_explicit_memory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ACHERON_STORE_BACKEND", "memory")
        store = create_worker_store()
        assert isinstance(store, InMemoryWorkerStore)

    def test_unknown_backend_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ACHERON_STORE_BACKEND", "cassandra")
        with pytest.raises(ValueError, match="Unknown ACHERON_STORE_BACKEND"):
            create_worker_store()


class TestCreateJobStore:
    def test_defaults_to_memory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ACHERON_STORE_BACKEND", raising=False)
        store = create_job_store()
        assert isinstance(store, InMemoryJobStore)

    def test_explicit_memory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ACHERON_STORE_BACKEND", "memory")
        store = create_job_store()
        assert isinstance(store, InMemoryJobStore)

    def test_unknown_backend_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ACHERON_STORE_BACKEND", "cassandra")
        with pytest.raises(ValueError, match="Unknown ACHERON_STORE_BACKEND"):
            create_job_store()
