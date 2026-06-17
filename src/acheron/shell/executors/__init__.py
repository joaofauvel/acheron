"""Executor implementations and factory."""

from __future__ import annotations

from typing import TYPE_CHECKING

from acheron.core.models import ExecutorStrategy
from acheron.shell.executors.async_executor import AsyncExecutor
from acheron.shell.executors.batch_async import BatchAsyncExecutor
from acheron.shell.executors.sequential import SequentialExecutor

if TYPE_CHECKING:
    from acheron.core.interfaces import Executor
    from acheron.shell.executors._utils import StepHandler


def create_executor(strategy: ExecutorStrategy, handler: StepHandler) -> Executor:
    """Create an executor instance for the given strategy."""
    match strategy:
        case ExecutorStrategy.SEQUENTIAL:
            return SequentialExecutor(handler)
        case ExecutorStrategy.ASYNC:
            return AsyncExecutor(handler)
        case ExecutorStrategy.BATCH_ASYNC:
            return BatchAsyncExecutor(handler)


__all__ = [
    "AsyncExecutor",
    "BatchAsyncExecutor",
    "SequentialExecutor",
    "create_executor",
]
