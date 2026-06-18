"""Tests for the store ABCs."""

import pytest

from acheron.shell.stores.base import JobStore, WorkerStore


class TestWorkerStoreAbstract:
    def test_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError, match="abstract"):
            WorkerStore()  # type: ignore[abstract]


class TestJobStoreAbstract:
    def test_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError, match="abstract"):
            JobStore()  # type: ignore[abstract]
